"""envdiff 单元测试：python3 -m unittest discover -s tests -v"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from envdiff import INTENTIONAL, SUSPECTED_OMISSION, TO_CONFIRM, compare
from envdiff.normalize import canonical, equivalence_reasons


class TestEquivalence(unittest.TestCase):
    """值等价但写法不同：不得报成差异。"""

    def equivalent(self, a, b):
        report = compare({"K": a}, {"K": b})
        self.assertEqual(report.diffs, [])
        self.assertEqual(len(report.equivalent), 1)
        return report.equivalent[0]

    def test_case_insensitive(self):
        item = self.equivalent("CN-NORTH", "cn-north")
        self.assertIn("大小写", item.reasons)

    def test_whitespace(self):
        item = self.equivalent("  hello   world ", "hello world")
        self.assertIn("空白字符", item.reasons)

    def test_quotes(self):
        item = self.equivalent('"abc"', "abc")
        self.assertIn("引号", item.reasons)

    def test_size_units(self):
        self.equivalent("512MB", "0.5GB")
        self.equivalent("1024KB", "1MB")
        self.equivalent("512 MB", "512mb")

    def test_time_units(self):
        self.equivalent("30s", "30000ms")
        self.equivalent("1.5min", "90s")

    def test_number_forms(self):
        self.equivalent("8", "8.0")
        self.equivalent("1000", "1000.00")

    def test_boolean_forms(self):
        self.equivalent("on", "true")
        self.equivalent("OFF", "false")

    def test_list_order_and_separator(self):
        item = self.equivalent("a, b,c", "c;b;a")
        self.assertIn("列表顺序/分隔符", item.reasons)

    def test_list_with_units_inside(self):
        self.equivalent("512MB, 30s", "30s, 0.5GB")

    def test_identical_not_reported(self):
        report = compare({"K": "v", "X": "1"}, {"K": "v", "X": "1"})
        self.assertEqual(report.identical_count, 2)
        self.assertEqual(report.equivalent, [])
        self.assertEqual(report.diffs, [])

    def test_real_difference_still_reported(self):
        report = compare({"K": "512MB"}, {"K": "1GB"})
        self.assertEqual(len(report.diffs), 1)
        self.assertEqual(report.diffs[0].category, TO_CONFIRM)


class TestClassification(unittest.TestCase):
    """分类：有意差异 / 待确认 / 疑似遗漏。"""

    def test_whitelist_intentional(self):
        report = compare(
            {"TTL": "300"}, {"TTL": "600"},
            whitelist=[{"key": "TTL", "env_a": "300", "env_b": "600",
                        "reason": "压测专用"}])
        item = report.diffs[0]
        self.assertEqual(item.category, INTENTIONAL)
        self.assertEqual(item.attribution, "whitelist")

    def test_whitelist_value_mismatch_falls_through(self):
        report = compare(
            {"TTL": "300"}, {"TTL": "999"},
            whitelist=[{"key": "TTL", "env_a": "300", "env_b": "600"}])
        self.assertEqual(report.diffs[0].category, TO_CONFIRM)

    def test_expected_per_env_intentional(self):
        report = compare(
            {"LVL": "INFO"}, {"LVL": "DEBUG"},
            expected={"LVL": {"env_a": "INFO", "env_b": "DEBUG"}})
        self.assertEqual(report.diffs[0].category, INTENTIONAL)
        self.assertEqual(report.diffs[0].attribution, "expected")

    def test_to_confirm_no_attribution(self):
        report = compare({"A": "1"}, {"A": "2"})
        item = report.diffs[0]
        self.assertEqual(item.category, TO_CONFIRM)
        self.assertEqual(item.attribution, "none")

    def test_missing_declared_is_omission(self):
        report = compare({"NEW": "1"}, {}, expected={"NEW": {}})
        item = report.diffs[0]
        self.assertEqual(item.category, SUSPECTED_OMISSION)
        self.assertEqual(item.attribution, "expected")

    def test_missing_in_baseline_is_omission(self):
        report = compare({"OLD": "x"}, {}, baseline={"OLD": "x"})
        item = report.diffs[0]
        self.assertEqual(item.category, SUSPECTED_OMISSION)
        self.assertEqual(item.attribution, "baseline")

    def test_missing_everywhere_declared_is_omission(self):
        report = compare({}, {}, expected={"MUST": {}})
        self.assertEqual(report.diffs[0].category, SUSPECTED_OMISSION)

    def test_missing_no_attribution_is_to_confirm(self):
        report = compare({"EXTRA": "1"}, {})
        self.assertEqual(report.diffs[0].category, TO_CONFIRM)

    def test_tuning_not_applied_is_omission(self):
        report = compare(
            {"CONN": "200"}, {"CONN": "100"},
            expected={"CONN": {"default": "100"}},
            baseline={"CONN": "200"})
        item = report.diffs[0]
        self.assertEqual(item.category, SUSPECTED_OMISSION)
        self.assertEqual(item.attribution, "baseline+expected")

    def test_drift_from_baseline_is_to_confirm(self):
        report = compare({"CONN": "200"}, {"CONN": "300"},
                         baseline={"CONN": "200"})
        self.assertEqual(report.diffs[0].category, TO_CONFIRM)

    def test_whitelist_disallow_missing(self):
        report = compare(
            {"REQ": "1"}, {},
            whitelist=[{"key": "REQ", "allow_missing": False}])
        self.assertEqual(report.diffs[0].category, SUSPECTED_OMISSION)


class TestSensitive(unittest.TestCase):
    """敏感参数：结果准确但不明文输出。"""

    SECRET = "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"

    def test_same_secret_not_a_diff(self):
        report = compare({"API_SECRET_KEY": "s3cr3t"}, {"API_SECRET_KEY": "s3cr3t"})
        self.assertEqual(report.diffs, [])
        self.assertEqual(report.identical_count, 1)

    def test_secret_diff_masked_everywhere(self):
        report = compare({"API_SECRET_KEY": "aaa"}, {"API_SECRET_KEY": "bbb"})
        item = report.diffs[0]
        self.assertTrue(item.sensitive)
        text = report.to_text()
        self.assertNotIn("aaa", text)
        self.assertNotIn("bbb", text)
        self.assertIn("sha256:", text)
        self.assertNotEqual(item.fingerprint_a, item.fingerprint_b)

    def test_cert_detected_by_value(self):
        report = compare({"BUNDLE": self.SECRET}, {"BUNDLE": self.SECRET + "x"})
        self.assertTrue(report.diffs[0].sensitive)

    def test_same_secret_different_format_is_equivalent(self):
        report = compare({"API_SECRET_KEY": " s3cr3t "}, {"API_SECRET_KEY": "s3cr3t"})
        self.assertEqual(report.diffs, [])
        self.assertEqual(len(report.equivalent), 1)
        self.assertNotIn("s3cr3t", report.to_text())

    def test_json_report_masks_secret(self):
        report = compare({"TOKEN": "aaa"}, {"TOKEN": "bbb"})
        blob = str(report.to_dict())
        self.assertNotIn("aaa", blob.replace("attribution", ""))
        self.assertNotIn("bbb", blob)


class TestEdgeCases(unittest.TestCase):
    """边界用例。"""

    def test_both_empty(self):
        report = compare({}, {})
        self.assertEqual(report.problem_count, 0)

    def test_empty_string_vs_missing(self):
        report = compare({"K": ""}, {})
        self.assertEqual(len(report.diffs), 1)

    def test_empty_string_vs_value(self):
        report = compare({"K": ""}, {"K": "v"})
        self.assertEqual(report.diffs[0].category, TO_CONFIRM)

    def test_unicode_values(self):
        report = compare({"NAME": "计费服务"}, {"NAME": "计费服务"})
        self.assertEqual(report.identical_count, 1)

    def test_numeric_json_values(self):
        report = compare({"PORT": 8080}, {"PORT": "8080"})
        self.assertEqual(report.identical_count, 1)
        report = compare({"PORT": 8080}, {"PORT": "8080.0"})
        self.assertEqual(len(report.equivalent), 1)

    def test_ambiguous_bare_letter_is_size(self):
        self.assertEqual(canonical("512m"), canonical("512MB"))

    def test_unknown_unit_is_plain_string(self):
        report = compare({"K": "10x"}, {"K": "10x"})
        self.assertEqual(report.identical_count, 1)

    def test_reasons_for_equivalence(self):
        reasons = equivalence_reasons("512MB", "0.5GB")
        self.assertIn("单位写法", reasons)


if __name__ == "__main__":
    unittest.main()
