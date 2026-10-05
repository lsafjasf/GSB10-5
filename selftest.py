#!/usr/bin/env python3
"""回滚验证库自测（标准库 unittest）。"""

import json
import os
import tempfile
import unittest

from rollback_check import gen
from rollback_check.oldreader import OldReader
from rollback_check.schema import SCHEMA_V1
from rollback_check.verify import (
    Verifier, build_report,
    ROLLBACK_OK, NEEDS_CONVERSION, NOT_ROLLBACKABLE,
)


class OldReaderTest(unittest.TestCase):
    def setUp(self):
        self.reader = OldReader(SCHEMA_V1)

    def test_v1_record_reads_fine(self):
        rec, issues = self.reader.read_record(
            {"id": 1, "name": "u", "status": "active"}, 1)
        self.assertEqual(rec["id"], 1)
        self.assertFalse([i for i in issues if i["severity"] == "error"])

    def test_unknown_enum_is_located_to_status(self):
        rec, issues = self.reader.read_record(
            {"id": 1, "name": "u", "status": "pending"}, 1)
        self.assertIsNone(rec)
        errors = [i for i in issues if i["severity"] == "error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["kind"], "unknown_enum")
        self.assertEqual(errors[0]["path"], "status")
        self.assertEqual(errors[0]["sample"], "pending")

    def test_additive_fields_are_info_only(self):
        rec, issues = self.reader.read_record(
            {"id": 1, "name": "u", "status": "active",
             "phone": "138", "tags": ["vip"]}, 1)
        self.assertIsNotNone(rec)
        self.assertNotIn("phone", rec)  # 被忽略，不进入旧版本视图
        infos = [i for i in issues if i["severity"] == "info"]
        self.assertEqual({i["path"] for i in infos}, {"phone", "tags"})

    def test_id_type_change_detected(self):
        rec, issues = self.reader.read_record(
            {"id": "u-00000001", "name": "u", "status": "active"}, 1)
        self.assertIsNone(rec)
        errors = [i for i in issues if i["severity"] == "error"]
        self.assertEqual(errors[0]["kind"], "type_mismatch")
        self.assertEqual(errors[0]["path"], "id")

    def test_missing_required_field(self):
        rec, issues = self.reader.read_record({"id": 1, "status": "active"}, 1)
        self.assertIsNone(rec)
        self.assertTrue(any(i["path"] == "name"
                            and i["kind"] == "missing_required_field"
                            for i in issues))

    def test_bool_is_not_int(self):
        rec, issues = self.reader.read_record(
            {"id": True, "name": "u", "status": "active"}, 1)
        self.assertIsNone(rec)
        self.assertTrue(any(i["path"] == "id" and i["kind"] == "type_mismatch"
                            for i in issues))

    def test_contract_functions_execute(self):
        rec, _ = self.reader.read_record(
            {"id": 1, "name": "u", "status": "banned"}, 1)
        self.assertEqual(self.reader.status_rank(rec), 2)
        self.assertEqual(self.reader.render_label(rec), "u <>")
        issues = self.reader.check_contracts(rec, 1)
        self.assertEqual(issues, [])

    def test_index_v1_ok_v2_rejected(self):
        self.assertEqual(self.reader.read_index(
            {"format": "flat-hash", "version": 1, "entries": {}}), [])
        issues = self.reader.read_index(
            {"format": "sharded-hash", "version": 2, "shards": {}})
        paths = {(i["kind"], i["path"]) for i in issues}
        self.assertEqual(paths,
                         {("index_format", "index.format"),
                          ("index_version", "index.version")})


class ScenarioTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="rollback_selftest_")
        cls.verifier = Verifier()
        cls.reports = {}
        for scenario in gen.SCENARIOS:
            data, index = gen.write_scenario(cls.tmp, scenario, 1000)
            stats = cls.verifier.verify_files(data, index)
            cls.reports[scenario] = build_report(scenario, stats)

    @classmethod
    def tearDownClass(cls):
        for fname in os.listdir(cls.tmp):
            os.remove(os.path.join(cls.tmp, fname))
        os.rmdir(cls.tmp)

    def test_full_compat_rollback_ok(self):
        r = self.reports["full_compat"]
        self.assertEqual(r["conclusion"], ROLLBACK_OK)
        self.assertEqual(r["read_failed"], 0)
        self.assertEqual(r["read_ok"], 1000)
        self.assertEqual(r["incompatibilities"], [])

    def test_additive_fields_rollback_ok_with_info(self):
        r = self.reports["additive_fields"]
        self.assertEqual(r["conclusion"], ROLLBACK_OK)
        self.assertEqual(r["read_failed"], 0)
        info_paths = {i["path"] for i in r["informational"]}
        self.assertEqual(info_paths, {"phone", "tags"})
        self.assertEqual(sum(i["count"] for i in r["informational"]), 2000)

    def test_new_enum_needs_conversion(self):
        r = self.reports["new_enum"]
        self.assertEqual(r["conclusion"], NEEDS_CONVERSION)
        self.assertEqual(r["read_failed"], 100)   # seq % 10 == 0
        self.assertEqual(r["read_ok"], 900)
        item = r["incompatibilities"][0]
        self.assertEqual(item["path"], "status")
        self.assertEqual(item["kind"], "unknown_enum")
        self.assertEqual(item["first_line"], 10)

    def test_index_change_needs_conversion(self):
        r = self.reports["index_change"]
        self.assertEqual(r["conclusion"], NEEDS_CONVERSION)
        kinds = {i["kind"] for i in r["incompatibilities"]}
        self.assertEqual(kinds, {"index_format", "index_version"})
        self.assertEqual(r["read_failed"], 0)  # 数据本身仍可读，只是索引不可用

    def test_id_type_change_not_rollbackable(self):
        r = self.reports["id_type_change"]
        self.assertEqual(r["conclusion"], NOT_ROLLBACKABLE)
        self.assertEqual(r["read_failed"], 1000)
        self.assertEqual(r["incompatibilities"][0]["path"], "id")

    def test_reports_are_json_serializable(self):
        for r in self.reports.values():
            json.dumps(r, ensure_ascii=False)


class PerfSmokeTest(unittest.TestCase):
    def test_perf_small(self):
        from rollback_check import perf
        result = perf.run_perf(count=10_000)
        self.assertEqual(result["read_ok"], 10_000)
        self.assertGreater(result["records_per_sec"], 0)
        self.assertGreater(result["peak_rss_mb"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
