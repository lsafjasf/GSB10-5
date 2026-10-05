"""自测：unittest，覆盖精确命中 / 降级 / 完全不支持 / 字段部分缺失，
以及确定性与独立 oracle 对拍。

运行：python -m export_negotiation.test_negotiator
"""

import json
import os
import random
import unittest

from . import negotiate, FORMATS, FIELD_TYPES
from .tests_oracle import oracle_negotiate

HERE = os.path.dirname(__file__)
GOLDEN = os.path.join(HERE, "golden_cases.jsonl")

BASE_FIELDS = [
    {"name": "id", "type": "number"},
    {"name": "title", "type": "string"},
    {"name": "created_at", "type": "date"},
]
RICH_FIELDS = BASE_FIELDS + [{"name": "body", "type": "richtext"}]
BLOB_FIELDS = [
    {"name": "id", "type": "number"},
    {"name": "attachment", "type": "binary"},
]
NESTED_FIELDS = [
    {"name": "id", "type": "number"},
    {"name": "meta", "type": "nested"},
]


class TestExact(unittest.TestCase):
    def test_json_exact(self):
        r = negotiate("json", BLOB_FIELDS)
        self.assertEqual(r["status"], "exact")
        self.assertEqual(r["selected_format"], "json")
        self.assertEqual(r["missing_fields"], [])

    def test_alias_and_case_insensitive(self):
        r = negotiate("  YML ", BASE_FIELDS)
        self.assertEqual(r["status"], "exact")
        self.assertEqual(r["selected_format"], "yaml")


class TestPartial(unittest.TestCase):
    def test_partial_when_no_format_covers_all(self):
        # binary + richtext 没有任何单一格式能同时覆盖 -> 部分成功
        fields = [
            {"name": "id", "type": "number"},
            {"name": "attachment", "type": "binary"},
            {"name": "body", "type": "richtext"},
        ]
        r = negotiate("csv", fields)
        self.assertEqual(r["status"], "partial")
        self.assertEqual(r["selected_format"], "csv")
        self.assertEqual(r["exported_fields"], ["id"])
        self.assertEqual(
            {m["name"] for m in r["missing_fields"]}, {"attachment", "body"})
        reasons = {m["name"]: m["reason"] for m in r["missing_fields"]}
        self.assertIn("二进制", reasons["attachment"])
        self.assertIn("富文本", reasons["body"])
        self.assertEqual(r["full_support_alternatives"], [])

    def test_pdf_partial_with_binary(self):
        fields = [{"name": "a", "type": "binary"}, {"name": "b", "type": "nested"}]
        r = negotiate("pdf", fields)
        self.assertEqual(r["status"], "partial")
        self.assertEqual({m["name"] for m in r["missing_fields"]}, {"a", "b"})


class TestDowngrade(unittest.TestCase):
    def test_csv_downgrades_to_json_for_nested(self):
        # csv 丢 nested -> 族内回退到同 kind 且全覆盖的 json
        r = negotiate("csv", NESTED_FIELDS)
        self.assertEqual(r["status"], "downgraded")
        self.assertEqual(r["selected_format"], "json")
        self.assertEqual(r["missing_fields"], [])
        self.assertEqual(r["requested_missing_fields"][0]["name"], "meta")

    def test_xlsx_downgrades_via_chain_to_pdf(self):
        # xlsx 支持 richtext，但请求的 richtext 字段 csv 不支持 ->
        # 降级链 csv 失败 -> 族内回退同 kind 的 pdf（支持 richtext）
        r = negotiate("xlsx", RICH_FIELDS)
        self.assertEqual(r["status"], "downgraded")
        self.assertEqual(r["selected_format"], "pdf")

    def test_xml_downgrades_to_json_for_binary(self):
        r = negotiate("xml", BLOB_FIELDS)
        self.assertEqual(r["status"], "downgraded")
        self.assertEqual(r["selected_format"], "json")

    def test_tsv_downgrades_to_csv_for_null(self):
        fields = BASE_FIELDS + [{"name": "deleted_at", "type": "null"}]
        r = negotiate("tsv", fields)
        self.assertEqual(r["status"], "downgraded")
        self.assertEqual(r["selected_format"], "csv")

    def test_pdf_downgrades_to_html_for_null(self):
        fields = BASE_FIELDS + [{"name": "deleted_at", "type": "null"}]
        r = negotiate("pdf", fields)
        self.assertEqual(r["status"], "downgraded")
        self.assertEqual(r["selected_format"], "html")


class TestFailed(unittest.TestCase):
    def test_unknown_format_lists_options(self):
        r = negotiate("docx", BASE_FIELDS)
        self.assertEqual(r["status"], "failed")
        self.assertIsNone(r["selected_format"])
        opts = r["available_options"]
        self.assertEqual([o["format"] for o in opts],
                         sorted(FORMATS, key=lambda n: (-FORMATS[n]["priority"], n)))
        for opt in opts:
            self.assertTrue(opt["reason"])
        # csv 能全覆盖基础字段
        csv_opt = next(o for o in opts if o["format"] == "csv")
        self.assertTrue(csv_opt["covers_all_fields"])
        self.assertEqual(csv_opt["missing_fields"], [])

    def test_failed_options_mark_missing_fields(self):
        r = negotiate("parquet", BLOB_FIELDS)
        self.assertEqual(r["status"], "failed")
        csv_opt = next(o for o in r["available_options"] if o["format"] == "csv")
        self.assertFalse(csv_opt["covers_all_fields"])
        self.assertEqual(csv_opt["missing_fields"][0]["name"], "attachment")
        json_opt = next(o for o in r["available_options"] if o["format"] == "json")
        self.assertTrue(json_opt["covers_all_fields"])


class TestDeterminism(unittest.TestCase):
    def test_repeated_results_identical(self):
        shuffled = NESTED_FIELDS[::-1]
        a = negotiate("csv", NESTED_FIELDS)
        b = negotiate("csv", shuffled)
        c = negotiate("CSV", NESTED_FIELDS)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(c, sort_keys=True))

    def test_deterministic_within_one_process(self):
        for _ in range(20):
            r1 = negotiate("docx", RICH_FIELDS)
            r2 = negotiate("docx", RICH_FIELDS)
            self.assertEqual(r1, r2)


class TestOracleCrossCheck(unittest.TestCase):
    def test_live_random_cross_check(self):
        rng = random.Random(42)
        tokens = list(FORMATS) + ["docx", "parquet", "avro", "xls", "xlsx2"]
        for _ in range(500):
            fmt = rng.choice(tokens)
            fields = [
                {"name": "g%d" % i, "type": rng.choice(FIELD_TYPES)}
                for i in range(rng.randint(0, 8))
            ]
            status, selected, missing = oracle_negotiate(fmt, fields)
            r = negotiate(fmt, fields)
            self.assertEqual(r["status"], status, (fmt, fields, r["status"], status))
            self.assertEqual(r["selected_format"], selected)
            self.assertEqual(
                sorted((m["name"], m["type"]) for m in r["missing_fields"]),
                sorted(missing),
            )

    @unittest.skipUnless(os.path.exists(GOLDEN), "golden_cases.jsonl 未生成")
    def test_golden_file(self):
        with open(GOLDEN, encoding="utf-8") as fh:
            cases = [json.loads(line) for line in fh if line.strip()]
        self.assertEqual(len(cases), 2000)
        for case in cases:
            r = negotiate(case["requested_format"], case["fields"])
            self.assertEqual(r["status"], case["expect_status"], case)
            self.assertEqual(r["selected_format"], case["expect_selected"])
            self.assertEqual(
                sorted((m["name"], m["type"]) for m in r["missing_fields"]),
                sorted(map(tuple, case["expect_missing"])),
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
