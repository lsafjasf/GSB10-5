#!/usr/bin/env python3
"""manifestdiff 自测（unittest，仅标准库）。"""
import copy
import unittest

from manifestdiff import (
    VERDICT_COMPLIANT,
    VERDICT_NEEDS_REVIEW,
    VERDICT_VIOLATION,
    compare_manifests,
)
from manifestdiff.core import NormalizeError, _NORMALIZERS


def vm(resource_id="vm-001", **overrides):
    record = {
        "id": resource_id,
        "type": "compute.vm",
        "region": "cn-east-1",
        "cpu": 4,
        "memory_gb": 16,
        "image": "ubuntu-22.04",
        "encrypted": True,
        "ports": [80, 443],
        "tags": {"env": "prod", "team": "core"},
        "description": "x",
        "owner": "ops@example.com",
    }
    record.update(overrides)
    return record


def pair_report(**cand_overrides):
    baseline = {"resources": [vm()]}
    candidate = {"resources": [vm(**cand_overrides)]}
    return compare_manifests(baseline, candidate)


class TestNormalization(unittest.TestCase):
    def test_cpu_equivalent_forms(self):
        norm = _NORMALIZERS["cpu"]
        for value in (4, "4", 4.0, "4.0"):
            self.assertEqual(norm(value), 4)

    def test_memory_units(self):
        norm = _NORMALIZERS["memory_gb"]
        self.assertEqual(norm("16GB"), 16)
        self.assertEqual(norm(" 16 gb "), 16)
        self.assertEqual(norm("16384MB"), 16)
        self.assertEqual(norm(16), 16)

    def test_bool_forms(self):
        norm = _NORMALIZERS["encrypted"]
        self.assertTrue(norm("TRUE"))
        self.assertFalse(norm("no"))
        self.assertTrue(norm(1))
        with self.assertRaises(NormalizeError):
            norm("maybe")

    def test_ports_order_and_string(self):
        norm = _NORMALIZERS["ports"]
        self.assertEqual(norm("443,80"), (80, 443))
        self.assertEqual(norm([443, 80, 80]), (80, 443))

    def test_tags_equivalent_forms(self):
        norm = _NORMALIZERS["tags"]
        self.assertEqual(norm(["env=prod", "team=core"]),
                         norm({"team": "core", "env": "prod"}))

    def test_token_case_insensitive(self):
        self.assertEqual(_NORMALIZERS["image"]("Ubuntu-22.04"), "ubuntu-22.04")


class TestScenarios(unittest.TestCase):
    def test_identical(self):
        """完全一致：零差异、合规。"""
        report = pair_report()
        self.assertEqual(report["summary"]["overall_verdict"], VERDICT_COMPLIANT)
        self.assertEqual(sum(report["summary"]["diff_counts"].values()), 0)
        self.assertEqual(report["summary"]["equivalent_notation_count"], 0)
        self.assertEqual(report["key_field_missing"], [])

    def test_field_and_resource_order_only(self):
        """仅顺序不同（资源顺序 + 对象字段顺序 + 端口顺序）：零差异、合规。"""
        baseline = {"resources": [vm("vm-001"), vm("vm-002", ports=[22]), vm("vm-003")]}
        reordered = {
            "resources": [
                dict(sorted(vm("vm-003").items(), reverse=True)),
                vm("vm-001", ports=[443, 80]),
                dict(sorted(vm("vm-002", ports=[22]).items())),
            ]
        }
        report = compare_manifests(baseline, reordered)
        counts = report["summary"]["diff_counts"]
        self.assertEqual(sum(counts.values()), 0)
        self.assertEqual(report["summary"]["overall_verdict"], VERDICT_COMPLIANT)
        kinds = {item["kind"] for item in report["equivalent_notations"]}
        self.assertIn("resource_order", kinds)
        self.assertIn("equivalent_notation", kinds)

    def test_equivalent_notations_not_diffs(self):
        """等价写法：不计入 diffs，记录在 equivalent_notations，判定合规。"""
        report = pair_report(
            type="Compute.VM", region="CN-EAST-1", cpu="4",
            memory_gb="16GB", image="Ubuntu-22.04", encrypted="TRUE",
            ports="443,80", tags=["env=prod", "team=core"],
        )
        counts = report["summary"]["diff_counts"]
        self.assertEqual(sum(counts.values()), 0)
        self.assertEqual(report["summary"]["overall_verdict"], VERDICT_COMPLIANT)
        noted_fields = {item["field"] for item in report["equivalent_notations"]}
        for field in ("type", "region", "cpu", "memory_gb",
                      "image", "encrypted", "ports", "tags"):
            self.assertIn(field, noted_fields)
        for item in report["equivalent_notations"]:
            self.assertEqual(item["verdict"], VERDICT_COMPLIANT)

    def test_key_field_missing_reported_separately(self):
        """关键字段缺失：单独进 key_field_missing，不混入 diffs，判定待确认。"""
        candidate = vm()
        del candidate["region"]
        candidate["encrypted"] = None
        report = compare_manifests({"resources": [vm()]},
                                   {"resources": [candidate]})
        missing_fields = {item["field"] for item in report["key_field_missing"]}
        self.assertIn("region", missing_fields)
        self.assertIn("encrypted", missing_fields)
        for item in report["key_field_missing"]:
            self.assertEqual(item["kind"], "missing_key_field")
            self.assertEqual(item["verdict"], VERDICT_NEEDS_REVIEW)
        diff_fields = {d.get("field") for d in report["diffs"]}
        self.assertNotIn("region", diff_fields)
        self.assertNotIn("encrypted", diff_fields)
        self.assertEqual(report["summary"]["overall_verdict"], VERDICT_NEEDS_REVIEW)

    def test_missing_identity_id(self):
        """id 缺失：无法匹配，单独报出。"""
        candidate = vm()
        del candidate["id"]
        report = compare_manifests({"resources": [vm()]},
                                   {"resources": [candidate]})
        kinds = {item["kind"] for item in report["key_field_missing"]}
        self.assertIn("missing_key_field", kinds)
        self.assertTrue(any(d["kind"] == "resource_missing" for d in report["diffs"]))

    def test_key_field_mismatch_is_violation(self):
        report = pair_report(memory_gb=64, encrypted=False)
        violations = [d for d in report["diffs"] if d["verdict"] == VERDICT_VIOLATION]
        self.assertEqual({d["field"] for d in violations}, {"memory_gb", "encrypted"})
        self.assertTrue(all(d["kind"] == "key_field_mismatch" for d in violations))
        self.assertEqual(report["summary"]["overall_verdict"], VERDICT_VIOLATION)

    def test_allowed_field_mismatch_is_compliant(self):
        report = pair_report(description="改过的描述", owner="dev@example.com")
        kinds = {d["kind"] for d in report["diffs"]}
        self.assertEqual(kinds, {"allowed_field_mismatch"})
        self.assertTrue(all(d["verdict"] == VERDICT_COMPLIANT for d in report["diffs"]))
        self.assertEqual(report["summary"]["overall_verdict"], VERDICT_COMPLIANT)

    def test_unknown_field_needs_review(self):
        report = pair_report(cost_center="CC-999")
        self.assertEqual(report["diffs"][0]["kind"], "unknown_field")
        self.assertEqual(report["diffs"][0]["verdict"], VERDICT_NEEDS_REVIEW)
        self.assertEqual(report["summary"]["overall_verdict"], VERDICT_NEEDS_REVIEW)

    def test_resource_add_remove(self):
        baseline = {"resources": [vm("vm-001"), vm("vm-002")]}
        candidate = {"resources": [vm("vm-001"), vm("vm-003")]}
        report = compare_manifests(baseline, candidate)
        by_id = {d["resource_id"]: d for d in report["diffs"]}
        self.assertEqual(by_id["vm-002"]["kind"], "resource_missing")
        self.assertEqual(by_id["vm-002"]["verdict"], VERDICT_VIOLATION)
        self.assertEqual(by_id["vm-003"]["kind"], "resource_added")
        self.assertEqual(by_id["vm-003"]["verdict"], VERDICT_NEEDS_REVIEW)
        self.assertEqual(report["summary"]["overall_verdict"], VERDICT_VIOLATION)

    def test_unparsable_key_field_is_violation(self):
        report = pair_report(memory_gb="sixteen cores")
        self.assertEqual(report["diffs"][0]["kind"], "key_field_unparsable")
        self.assertEqual(report["diffs"][0]["verdict"], VERDICT_VIOLATION)

    def test_report_is_json_serializable(self):
        import json
        report = pair_report(memory_gb=64, cost_center="x")
        json.dumps(report, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
