"""协商逻辑自测：覆盖精确命中、降级、完全不支持、字段部分缺失、确定性。"""

import json
import unittest

from export_negotiation import (
    DOWNGRADED,
    EXACT,
    PARTIAL,
    UNSUPPORTED,
    negotiate,
)

SCALAR_FIELDS = [("id", "int"), ("name", "str"), ("created", "date")]
NESTED_FIELDS = SCALAR_FIELDS + [("tags", "array"), ("meta", "object")]
BINARY_ONLY = [("blob", "binary")]


class ExactMatchTests(unittest.TestCase):
    def test_exact_hit(self):
        result = negotiate("json", SCALAR_FIELDS)
        self.assertEqual(result["status"], EXACT)
        self.assertEqual(result["format"], "json")
        self.assertEqual(result["family"], "json")
        self.assertEqual(result["missing_fields"], [])
        self.assertEqual(result["exportable_fields"], ["id", "name", "created"])

    def test_alias_counts_as_exact(self):
        result = negotiate("jsonl", SCALAR_FIELDS)
        self.assertEqual(result["status"], EXACT)
        self.assertEqual(result["format"], "ndjson")
        self.assertIn("规范化为 ndjson", result["trace"][0])

    def test_exact_format_with_partial_fields(self):
        result = negotiate("csv", NESTED_FIELDS)
        self.assertEqual(result["status"], PARTIAL)
        self.assertEqual(result["format"], "csv")
        self.assertEqual(result["exportable_fields"], ["id", "name", "created"])
        self.assertEqual(
            [m["name"] for m in result["missing_fields"]], ["tags", "meta"]
        )
        for item in result["missing_fields"]:
            self.assertIn("csv", item["reason"])


class DowngradeTests(unittest.TestCase):
    def test_known_format_downgrades_within_family(self):
        result = negotiate("xlsx", SCALAR_FIELDS)
        self.assertEqual(result["status"], DOWNGRADED)
        self.assertEqual(result["format"], "csv")
        self.assertEqual(result["family"], "tabular")
        self.assertEqual(result["missing_fields"], [])

    def test_known_format_downgrade_partial(self):
        result = negotiate("xlsx", NESTED_FIELDS)
        self.assertEqual(result["status"], PARTIAL)
        self.assertEqual(result["format"], "csv")
        self.assertEqual(
            [m["name"] for m in result["missing_fields"]], ["tags", "meta"]
        )
        # 选项列表里 json 应排在 csv 之前（覆盖率更高）
        self.assertEqual(result["options"][0]["format"], "json")
        self.assertEqual(result["options"][0]["coverage"], 1.0)

    def test_unknown_format_picks_best_coverage(self):
        result = negotiate("yaml", NESTED_FIELDS)
        self.assertEqual(result["status"], DOWNGRADED)
        self.assertEqual(result["format"], "json")

    def test_unknown_format_scalar_fields(self):
        result = negotiate("yaml", SCALAR_FIELDS)
        self.assertEqual(result["status"], DOWNGRADED)
        # 覆盖率并列时按 preference 升序：json(5) < csv(10)
        self.assertEqual(result["format"], "json")


class UnsupportedTests(unittest.TestCase):
    def test_binary_only_is_unsupported(self):
        result = negotiate("parquet", BINARY_ONLY)
        self.assertEqual(result["status"], UNSUPPORTED)
        self.assertIsNone(result["format"])
        self.assertEqual(result["exportable_fields"], [])
        self.assertEqual([m["name"] for m in result["missing_fields"]], ["blob"])
        self.assertIn("binary", result["missing_fields"][0]["reason"])

    def test_failure_returns_options_with_reasons(self):
        result = negotiate("parquet", BINARY_ONLY)
        self.assertTrue(result["options"])
        for opt in result["options"]:
            self.assertIn("reason", opt)
            self.assertIn("coverage", opt)
            self.assertIn("missing_fields", opt)
            self.assertEqual(opt["coverage"], 0.0)

    def test_exact_format_with_binary_field_is_partial(self):
        result = negotiate("json", BINARY_ONLY)
        self.assertEqual(result["status"], PARTIAL)
        self.assertEqual(result["missing_fields"][0]["name"], "blob")


class DeterminismTests(unittest.TestCase):
    CASES = [
        ("json", SCALAR_FIELDS),
        ("jsonl", SCALAR_FIELDS),
        ("csv", NESTED_FIELDS),
        ("xlsx", SCALAR_FIELDS),
        ("xlsx", NESTED_FIELDS),
        ("yaml", NESTED_FIELDS),
        ("parquet", BINARY_ONLY),
        ("json", BINARY_ONLY),
        ("ods", SCALAR_FIELDS),
        ("avro", NESTED_FIELDS),
    ]

    def test_repeated_negotiation_is_identical(self):
        for fmt, fields in self.CASES:
            first = json.dumps(negotiate(fmt, fields), sort_keys=True)
            for _ in range(50):
                again = json.dumps(negotiate(fmt, fields), sort_keys=True)
                self.assertEqual(first, again, "非确定性结果: %s" % fmt)

    def test_result_is_json_serializable(self):
        for fmt, fields in self.CASES:
            json.dumps(negotiate(fmt, fields))  # 不抛异常即可


class InputValidationTests(unittest.TestCase):
    def test_unknown_field_type_raises(self):
        with self.assertRaises(ValueError):
            negotiate("json", [("x", "weird_type")])

    def test_empty_format_raises(self):
        with self.assertRaises(ValueError):
            negotiate("", SCALAR_FIELDS)

    def test_empty_fields_exact(self):
        result = negotiate("json", [])
        self.assertEqual(result["status"], EXACT)
        self.assertEqual(result["exportable_fields"], [])


if __name__ == "__main__":
    unittest.main()
