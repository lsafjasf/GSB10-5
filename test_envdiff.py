"""envdiff 自测：覆盖完全一致、仅写法不同、合理差异、疑似遗漏、敏感参数、边界用例。"""

import unittest

import envdiff
from envdiff import (EQUAL, EQUIVALENT, INTENTIONAL, NEEDS_CONFIRM,
                     SUSPECTED_MISS, WhitelistRule, compare_envs,
                     is_sensitive_key, normalize_value, values_equivalent)


class TestNormalize(unittest.TestCase):
    def test_case_and_whitespace(self):
        self.assertTrue(values_equivalent("  True ", "TRUE"))
        self.assertTrue(values_equivalent("a  b", "a b"))

    def test_units(self):
        self.assertTrue(values_equivalent("1GB", "1024MB"))
        self.assertTrue(values_equivalent("2gb", "2048 mb"))
        self.assertTrue(values_equivalent("1000ms", "1s"))
        self.assertTrue(values_equivalent("2min", "120000ms"))
        self.assertFalse(values_equivalent("1GB", "1000MB"))

    def test_list_order(self):
        self.assertTrue(values_equivalent("a,b,c", "c, a ,b"))
        self.assertTrue(values_equivalent("x;y", "y,x"))
        self.assertFalse(values_equivalent("a,b", "a,c"))

    def test_case_sensitive_opt_in(self):
        self.assertFalse(values_equivalent("AbC", "abc", case_sensitive=True))


class TestSensitive(unittest.TestCase):
    def test_key_detection(self):
        for k in ("db.password", "API_KEY", "tls.cert", "app.secret.token"):
            self.assertTrue(is_sensitive_key(k))
        self.assertFalse(is_sensitive_key("db.host"))

    def test_report_hides_plaintext(self):
        report = compare_envs({"db.password": "s3cret-a"},
                              {"db.password": "s3cret-b"})
        item = report.items[0]
        self.assertEqual(item.category, SUSPECTED_MISS)
        self.assertTrue(item.sensitive)
        self.assertNotIn("s3cret", str(item.to_dict()))
        self.assertNotEqual(item.value_a, item.value_b)  # 指纹不同

    def test_sensitive_equal_by_hash(self):
        report = compare_envs({"db.password": "same"}, {"db.password": "same"})
        self.assertEqual(report.items[0].category, EQUAL)
        self.assertEqual(report.items[0].value_a, report.items[0].value_b)

    def test_sensitive_no_casefold(self):
        # 敏感值不做大小写折叠，Password != password
        report = compare_envs({"db.password": "ABC"}, {"db.password": "abc"})
        self.assertNotEqual(report.items[0].category, EQUIVALENT)


class TestClassification(unittest.TestCase):
    def test_identical(self):
        r = compare_envs({"a": "1", "b": "x"}, {"a": "1", "b": "x"})
        self.assertEqual(r.summary()[EQUAL], 2)

    def test_format_only_diff(self):
        r = compare_envs({"heap": "2GB", "hosts": "a,b"},
                         {"heap": "2048MB", "hosts": "b, a"})
        self.assertEqual(r.summary()[EQUIVALENT], 2)
        self.assertEqual(r.summary()[SUSPECTED_MISS], 0)

    def test_declared_expected(self):
        expected = {"region": {"env_a": "cn", "env_b": "us", "reason": "按地域部署"}}
        r = compare_envs({"region": "cn"}, {"region": "us"}, expected=expected)
        self.assertEqual(r.items[0].category, INTENTIONAL)
        # 实际值与声明不符 -> 疑似遗漏
        r2 = compare_envs({"region": "cn"}, {"region": "eu"}, expected=expected)
        self.assertEqual(r2.items[0].category, SUSPECTED_MISS)

    def test_whitelist(self):
        wl = [WhitelistRule(key="cache.*", reason="缓存容量按机型"),
              WhitelistRule(key="log.level", values=[["info", "debug"]])]
        r = compare_envs({"cache.size": "4GB", "log.level": "INFO"},
                         {"cache.size": "8GB", "log.level": "debug"},
                         whitelist=wl)
        self.assertEqual(r.summary()[INTENTIONAL], 2)
        # 白名单限定值对，不在值对内不豁免
        r2 = compare_envs({"log.level": "INFO"}, {"log.level": "ERROR"}, whitelist=wl)
        self.assertEqual(r2.items[0].category, SUSPECTED_MISS)

    def test_baseline_known_diff_needs_confirm(self):
        baseline = {"thread.pool": {"env_a": "8", "env_b": "16"}}
        r = compare_envs({"thread.pool": "8"}, {"thread.pool": "16"}, baseline=baseline)
        self.assertEqual(r.items[0].category, NEEDS_CONFIRM)

    def test_baseline_was_equal_suspected_miss(self):
        baseline = {"timeout": {"env_a": "30", "env_b": "30"}}
        r = compare_envs({"timeout": "30"}, {"timeout": "60"}, baseline=baseline)
        self.assertEqual(r.items[0].category, SUSPECTED_MISS)

    def test_missing_key(self):
        r = compare_envs({"a": "1", "b": "2"}, {"a": "1"})
        miss = r.by_category(SUSPECTED_MISS)
        self.assertEqual(len(miss), 1)
        self.assertEqual(miss[0].key, "b")
        # 白名单可豁免缺失
        wl = [WhitelistRule(key="b", reason="仅 A 环境需要")]
        r2 = compare_envs({"a": "1", "b": "2"}, {"a": "1"}, whitelist=wl)
        self.assertEqual(r2.by_category(INTENTIONAL)[0].key, "b")

    def test_new_diff_no_attribution(self):
        r = compare_envs({"x": "1"}, {"x": "2"})
        self.assertEqual(r.items[0].category, SUSPECTED_MISS)


class TestEdgeCases(unittest.TestCase):
    def test_empty_envs(self):
        self.assertEqual(compare_envs({}, {}).items, [])

    def test_empty_vs_nonempty_value(self):
        r = compare_envs({"k": ""}, {"k": "v"})
        self.assertEqual(r.items[0].category, SUSPECTED_MISS)

    def test_unicode(self):
        self.assertTrue(values_equivalent("中文 值", "中文 值"))
        r = compare_envs({"k": "中文"}, {"k": "中文"})
        self.assertEqual(r.items[0].category, EQUAL)

    def test_load_env_file(self):
        import os, tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".properties",
                                         delete=False, encoding="utf-8") as f:
            f.write("# comment\na=1\nb: hello world\nempty=\n")
            path = f.name
        try:
            params = envdiff.load_env_file(path)
        finally:
            os.unlink(path)
        self.assertEqual(params, {"a": "1", "b": "hello world", "empty": ""})

    def test_load_json_env(self):
        import os, tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False, encoding="utf-8") as f:
            f.write('{"x": 1, "y": "s"}')
            path = f.name
        try:
            params = envdiff.load_env_file(path)
        finally:
            os.unlink(path)
        self.assertEqual(params, {"x": "1", "y": "s"})

    def test_markdown_report_no_secret_leak(self):
        r = compare_envs({"db.password": "topsecret1"}, {"db.password": "topsecret2"})
        md = envdiff.render_markdown(r)
        self.assertNotIn("topsecret", md)
        self.assertIn("疑似遗漏", md)


if __name__ == "__main__":
    unittest.main(verbosity=2)
