#!/usr/bin/env python3
"""scope.py 自测：判定表、越权用例、非法作用域、通过率统计。

运行：python3 selftest.py
任一预期不符则以退出码 1 失败。
"""

import sys

from scope import ScopeError, check, covers, parse_scope


# ---------------------------------------------------------------------------
# 1) 正常授权用例：全部必须放行
# 每条 = (令牌授权作用域列表, 请求作用域, 命中的授权项)
# ---------------------------------------------------------------------------
LEGIT_CASES = [
    (["files.docs:read"], "files.docs:read", "files.docs:read"),                       # 精确匹配
    (["files.**:read"], "files:read", "files.**:read"),                                # 上级作用域，** 匹配零段
    (["files.**:read"], "files.docs.2024:read", "files.**:read"),                      # 上级作用域，** 匹配多段
    (["files.*:read"], "files.docs:read", "files.*:read"),                             # 单层通配
    (["files.*.tmp:read"], "files.docs.tmp:read", "files.*.tmp:read"),                 # 中段通配
    (["files.docs:readwrite"], "files.docs:read", "files.docs:readwrite"),             # 读写蕴含读
    (["files.docs:readwrite"], "files.docs:write", "files.docs:readwrite"),            # 读写蕴含写
    (["files.docs:*"], "files.docs:write", "files.docs:*"),                            # action 通配
    (["files.**:readwrite"], "files.docs.secret:write", "files.**:readwrite"),         # 上级 + 读写蕴含写
    (["a.b:read", "files.**:readwrite", "c.d:write"], "files.x:write",
     "files.**:readwrite"),                                                            # 多授权项命中其一
    (["files.**:read", "files.docs:write"], "files.docs:write", "files.docs:write"),   # 精确项优先于宽授权
    (["*:read"], "files:read", "*:read"),                                              # resource 顶层通配
]


# ---------------------------------------------------------------------------
# 2) 越权用例：全部必须拒绝
#    类别 = 前缀互含 / 通配混用 / 拼写近似
# ---------------------------------------------------------------------------
ESCALATION_CASES = [
    # --- 字符串前缀互含：朴素 startswith 会误放行 ---
    ("前缀互含", ["files.public:read"], "files.public2:read"),
    ("前缀互含", ["files.publicity:read"], "files.public:read"),
    ("前缀互含", ["files:read"], "files2:read"),
    ("前缀互含", ["files:read"], "files.evil:read"),
    ("前缀互含", ["files.docs:read"], "files.docs.tmp:read"),
    # --- 通配混用：把 * / ** 当字符串后缀会误放行 ---
    ("通配混用", ["files.*:read"], "files.a.b:read"),       # * 只匹配一段
    ("通配混用", ["files.*:read"], "files:read"),           # * 不能匹配零段
    ("通配混用", ["files.*:read"], "files.x:write"),        # resource 通配不等于 action 通配
    ("通配混用", ["files.*.tmp:read"], "files.a.b.tmp:read"),
    ("通配混用", ["files.**:read"], "other:read"),          # ** 也不能越过已固定的字面段
    ("通配混用", ["files.*:*"], "files.a.b:read"),          # 双通配也受段数约束
    # --- 拼写近似：形似但不同名 / 不同 action ---
    ("拼写近似", ["files.docs:read"], "files.doc:read"),
    ("拼写近似", ["files.docs:read"], "files.docs:reads"),  # 注意：整体非法，必须拒
    ("拼写近似", ["files.docs:read"], "files.d0cs:read"),
    ("拼写近似", ["user.settings:write"], "users.settings:write"),
    ("拼写近似", ["files.docs:readwrite"], "files.docs:rd"),
    # --- 读 vs 读写反向越权：持读求写 ---
    ("权限方向", ["files.docs:read"], "files.docs:write"),
]


# ---------------------------------------------------------------------------
# 3) 非法/空作用域：解析必须抛 ScopeError，check 必须拒绝并给原因
# ---------------------------------------------------------------------------
ILLEGAL_CASES = [
    "",                         # 空字符串
    "   ",                      # 纯空白
    "files.docs",               # 缺 action / 冒号
    "files.docs:",              # action 为空
    ":read",                    # resource 为空
    "files..docs:read",         # 空段
    ".files:read",              # 首段为空
    "files.:read",              # 末段为空
    "files.**.x:read",          # ** 不在末段
    "files.*:read",             # 请求作用域含通配（经 check 判定）
    "files.docs:execute",       # 未知 action
    "Files.docs:read",          # 大写
    "files.docs:read write",    # action 含空格
    "files.docs:read:write",    # 多个冒号
    "files.do cs:read",         # resource 含空格
]


def _row(cols, widths):
    return " | ".join(str(c).ljust(w) for c, w in zip(cols, widths))


def main() -> int:
    failures = []

    print("=" * 92)
    print("一、正常授权判定表（应全部 ALLOW）")
    print("=" * 92)
    headers = ["#", "请求作用域", "授权作用域", "结果", "命中项 / 原因"]
    widths = [3, 28, 52, 7, 24]
    print(_row(headers, widths))
    print("-" * 92)
    for i, (grants, req, expect_match) in enumerate(LEGIT_CASES, 1):
        d = check(grants, req)
        note = d.matched_grant or d.reason
        print(_row([i, req, ",".join(grants), "ALLOW" if d.allowed else "DENY", note], widths))
        if not d.allowed:
            failures.append(f"正常用例被拒: {req} (授权={grants}, 原因={d.reason})")
        elif d.matched_grant != expect_match:
            failures.append(f"命中项不符: {req} 期望 {expect_match} 实际 {d.matched_grant}")

    print()
    print("=" * 92)
    print("二、越权用例判定表（应全部 DENY）")
    print("=" * 92)
    headers = ["#", "类别", "请求作用域", "授权作用域", "结果", "拒绝原因"]
    widths = [3, 8, 26, 26, 7, 30]
    print(_row(headers, widths))
    print("-" * 92)
    for i, (kind, grants, req) in enumerate(ESCALATION_CASES, 1):
        d = check(grants, req)
        print(_row([i, kind, req, ",".join(grants), "ALLOW" if d.allowed else "DENY", d.reason], widths))
        if d.allowed:
            failures.append(f"越权用例被放行: {req} (授权={grants}, 命中={d.matched_grant})")

    print()
    print("=" * 92)
    print("三、非法 / 空作用域判定（解析应报错，check 应 DENY 并说明原因）")
    print("=" * 92)
    headers = ["#", "输入作用域", "parse 报错 / check 拒绝原因"]
    widths = [3, 26, 62]
    print(_row(headers, widths))
    print("-" * 92)
    for i, raw in enumerate(ILLEGAL_CASES, 1):
        parse_reason = ""
        try:
            parse_scope(raw, allow_wildcard=False)  # 请求侧规则：必须具体
            parse_reason = "未报错(?) "
        except ScopeError as exc:
            parse_reason = str(exc)
        d = check(["files.**:readwrite"], raw)
        shown = f"{parse_reason}  =>  check: DENY, {d.reason}"
        print(_row([i, repr(raw), shown], widths))
        if not parse_reason or parse_reason.startswith("未报错"):
            failures.append(f"非法作用域未被 parse_scope 拒绝: {raw!r}")
        if d.allowed:
            failures.append(f"非法请求被 check 放行: {raw!r}")

    # 授权列表里混入非法项：合法项仍可用，非法项永不放行
    print()
    print("四、授权侧 fail-closed 检查")
    print("-" * 92)
    d = check(["files.**:read", "bad scope!!", "files.docs:write"], "files.docs:read")
    print(f"  含非法项的授权列表校验 files.docs:read -> {'ALLOW' if d.allowed else 'DENY'} (命中 {d.matched_grant})")
    if not d.allowed or d.matched_grant != "files.**:read":
        failures.append("合法授权项应在混入非法项时继续生效")
    d = check(["bad scope!!", "Files.X:read"], "files.x:read")
    print(f"  全部非法授权列表校验 files.x:read -> {'ALLOW' if d.allowed else 'DENY'} ({d.reason})")
    if d.allowed:
        failures.append("全部非法的授权列表必须拒绝")
    d = check([], "files.docs:read")
    print(f"  空授权列表校验 files.docs:read -> {'ALLOW' if d.allowed else 'DENY'} ({d.reason})")
    if d.allowed:
        failures.append("空授权列表必须拒绝")

    # 底层 covers 的快速边界断言（精确/上级/通配/段数）
    assert not covers(parse_scope("files.*:read"), parse_scope("files.a.b:read", allow_wildcard=False))
    assert covers(parse_scope("files.**:read"), parse_scope("files:read", allow_wildcard=False))
    assert not covers(parse_scope("files:read"), parse_scope("files2:read", allow_wildcard=False))
    assert not covers(parse_scope("files.docs:read"), parse_scope("files.docs:write", allow_wildcard=False))

    print()
    print("=" * 92)
    total_legit = len(LEGIT_CASES)
    legit_pass = total_legit - sum(1 for f in failures if "正常用例" in f) - sum(
        1 for f in failures if "命中项不符" in f
    )
    total_escal = len(ESCALATION_CASES)
    escal_blocked = total_escal - sum(1 for f in failures if "越权用例" in f)
    print(f"正常授权通过率：{legit_pass}/{total_legit} = {legit_pass / total_legit:.1%}")
    print(f"越权拦截率：  {escal_blocked}/{total_escal} = {escal_blocked / total_escal:.1%}")
    print("=" * 92)

    if failures:
        print("\nFAIL:")
        for f in failures:
            print(" -", f)
        return 1
    print("\nALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
