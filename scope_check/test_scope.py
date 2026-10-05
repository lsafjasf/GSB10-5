"""scope.py 自测：正常授权、越权（前缀互含/通配混用/拼写近似）、非法作用域。

运行: python3 test_scope.py -v
"""

import unittest

from scope import ScopeError, check, covers, is_valid_scope, parse_scope


# ---------------------------------------------------------------- 正常授权（必须全部通过）
ALLOW_CASES = [
    # (grant, request, 类别)
    ("files.read", "files.read", "精确匹配"),
    ("*", "*", "精确匹配-根通配"),
    ("files", "files", "精确匹配-单段"),
    ("files", "files.read", "上级作用域"),
    ("files", "files.read.chunk", "上级作用域-跨多级"),
    ("files.read", "files.read.chunk", "上级作用域-中间级"),
    ("*", "anything.deep.here", "根通配覆盖任意下级"),
    ("files.*", "files.read", "通配作用域-单段"),
    ("files.*", "files.read.chunk", "通配作用域-其下级"),
    ("files.*.read", "files.doc.read", "通配作用域-中间段"),
    ("files.*", "files.*", "通配授权覆盖相同通配请求"),
    ("a.b.c", "a.b.c.d.e", "多段上级"),
]

# ---------------------------------------------------------------- 越权（必须全部拒绝）
DENY_CASES = [
    # (grant, request, 类别)
    # —— 前缀互含：字符串前缀成立但段级不成立 ——
    ("files.read", "files.readonly", "前缀互含: read 是 readonly 的字符串前缀"),
    ("files.readonly", "files.read", "前缀互含: 反向"),
    ("app", "app2.read", "前缀互含: app 是 app2 的字符串前缀"),
    ("files.read", "files.read2.chunk", "前缀互含: 跨层级"),
    ("files.read", "files", "前缀互含: 子级授权不覆盖上级请求"),
    ("files.read.chunk", "files.read", "前缀互含: 深层授权不覆盖浅层请求"),
    # —— 通配混用 ——
    ("files.*", "files", "通配混用: 通配段必须匹配一个真实段"),
    ("files.*.read", "files.read", "通配混用: 通配不跨段"),
    ("files.*", "other.read", "通配混用: 字面段不匹配"),
    ("*", "files", "通配混用: 根通配不覆盖… 注意此项实际允许", ),  # 占位，见下方修正
    ("files.read*", "files.readonly", "通配混用: read* 非法授权"),
    ("files.**", "files.read", "通配混用: ** 非法授权"),
    ("files.*x", "files.ax", "通配混用: *x 非法授权"),
    # —— 拼写近似 ——
    ("files.read", "files.raed", "拼写近似: 字母换位"),
    ("files.read", "files.read1", "拼写近似: 尾部加字符"),
    ("files.read", "files.reaad", "拼写近似: 重复字母"),
    ("files.read", "Files.read", "拼写近似: 大小写（请求非法）"),
    ("files.read", "files.read ", "拼写近似: 尾部空格（请求非法）"),
    ("files.read", "files.read\t", "拼写近似: 尾部制表符（请求非法）"),
    ("files.read", "files．read", "拼写近似: 全角点（请求非法）"),
]
# 修正：根通配 "*" 按规则确实覆盖 "files"，从拒绝表中移除
DENY_CASES = [c for c in DENY_CASES if c[0:2] != ("*", "files")]

# ---------------------------------------------------------------- 非法/空作用域（必须拒绝并给出原因）
INVALID_SCOPES = [
    ("", "空字符串"),
    (".", "仅分隔符"),
    ("files.", "尾点"),
    (".files", "前导点"),
    ("files..read", "连续点"),
    ("files.read*", "通配符与其他字符混用"),
    ("**", "双星号非法"),
    ("Files", "大写字母"),
    ("1abc", "数字开头"),
    ("files.read chunk", "含空白"),
    ("a" * 64, "段超长(64>63)"),
    ("files.read*", "部分通配"),
    (None, "非字符串 None"),
    (123, "非字符串 int"),
    (["files"], "非字符串 list"),
]


class TestAllow(unittest.TestCase):
    def test_all_allow_cases_pass(self):
        passed, failed = 0, []
        for grant, request, category in ALLOW_CASES:
            result = check(grant, request)
            if result.allowed:
                passed += 1
            else:
                failed.append((grant, request, category, result.reason))
        total = len(ALLOW_CASES)
        print("\n[正常授权] 通过 %d/%d (%.1f%%)" % (passed, total, 100.0 * passed / total))
        for grant, request, category, reason in failed:
            print("  未通过: grant=%r request=%r [%s] 原因: %s" % (grant, request, category, reason))
        self.assertEqual(passed, total, "存在未通过的正常授权用例: %r" % (failed,))

    def test_covers_bool_api(self):
        for grant, request, _ in ALLOW_CASES:
            self.assertTrue(covers(grant, request), (grant, request))


class TestPrivilegeEscalation(unittest.TestCase):
    def test_all_escalation_cases_denied(self):
        denied, leaked = 0, []
        for grant, request, category in DENY_CASES:
            result = check(grant, request)
            if not result.allowed:
                denied += 1
            else:
                leaked.append((grant, request, category))
        total = len(DENY_CASES)
        print("\n[越权用例] 拒绝 %d/%d (%.1f%%)" % (denied, total, 100.0 * denied / total))
        for grant, request, category in DENY_CASES:
            result = check(grant, request)
            status = "拒绝" if not result.allowed else "!!放行!!"
            print("  [%s] grant=%r request=%r -> %s (%s)"
                  % (category, grant, request, status, result.reason))
        self.assertEqual(denied, total, "存在被放行的越权用例: %r" % (leaked,))


class TestInvalidScopes(unittest.TestCase):
    def test_invalid_scopes_rejected_with_reason(self):
        print("\n[非法/空作用域] 拒绝结果:")
        for scope, note in INVALID_SCOPES:
            self.assertFalse(is_valid_scope(scope), (scope, note))
            with self.assertRaises(ScopeError) as ctx:
                parse_scope(scope)
            print("  %-14s scope=%-12r 原因: %s" % (note, scope, ctx.exception))
            # 非法作用域参与校验时 fail-closed
            self.assertFalse(covers(scope, "files.read"))
            self.assertFalse(covers("files.read", scope))

    def test_empty_scope_reason(self):
        result = check("", "files.read")
        self.assertFalse(result.allowed)
        self.assertIn("空", result.reason)
        result = check("files.read", "")
        self.assertFalse(result.allowed)
        self.assertIn("空", result.reason)


class TestSemantics(unittest.TestCase):
    def test_wildcard_is_whole_segment_only(self):
        self.assertTrue(covers("a.*", "a.b"))
        self.assertFalse(covers("a.*", "a"))          # 通配必须匹配一段
        self.assertFalse(is_valid_scope("a.b*"))      # 部分通配非法
        self.assertFalse(is_valid_scope("a.**"))      # 双星非法

    def test_no_string_prefix_logic(self):
        # 字符串前缀成立但段级不成立，必须拒绝
        self.assertTrue("files.readonly".startswith("files.read"))
        self.assertFalse(covers("files.read", "files.readonly"))

    def test_case_sensitive(self):
        self.assertFalse(is_valid_scope("Files.Read"))
        self.assertFalse(covers("files.read", "FILES.READ"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
