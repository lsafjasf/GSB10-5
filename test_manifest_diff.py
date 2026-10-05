#!/usr/bin/env python3
"""manifest_diff 自测（标准库 unittest）。

运行：python3 test_manifest_diff.py -v
"""

import json
import unittest

from manifest_diff import (
    CAT_ENTRY_EXTRA,
    CAT_ENTRY_MISSING,
    CAT_MISSING_CRITICAL,
    VERDICT_COMPLIANT,
    VERDICT_PENDING,
    VERDICT_VIOLATION,
    compare_manifests,
)


def make_entry(name, **fields):
    entry = {
        "name": name,
        "type": "rpm",
        "version": "1.2.0",
        "checksum": "ab12cd34",
        "size": 1024,
        "path": "/opt/app/bin",
        "description": "base package",
        "tags": ["core", "net"],
    }
    entry.update(fields)
    return entry


def manifest(*entries):
    return {"resources": list(entries)}


class TestIdenticalAndOrder(unittest.TestCase):
    def test_completely_identical(self):
        a = manifest(make_entry("pkg-a"), make_entry("pkg-b"))
        b = manifest(make_entry("pkg-a"), make_entry("pkg-b"))
        result = compare_manifests(a, b)
        self.assertEqual(result["summary"]["verdict"], "完全一致")
        self.assertEqual(result["summary"]["violation_count"], 0)
        self.assertEqual(result["summary"]["compliant_diff_count"], 0)
        self.assertEqual(result["summary"]["pending_count"], 0)

    def test_entry_order_and_field_order_ignored(self):
        # 条目顺序不同 + JSON 字段顺序不同，不得产生任何差异。
        text_a = json.dumps(manifest(
            make_entry("pkg-a"), make_entry("pkg-b")))
        entry_b1 = json.loads(json.dumps(make_entry("pkg-b")),
                              object_pairs_hook=lambda p: dict(reversed(p)))
        entry_b2 = json.loads(json.dumps(make_entry("pkg-a")),
                              object_pairs_hook=lambda p: dict(reversed(p)))
        text_b = json.dumps(manifest(entry_b1, entry_b2))
        result = compare_manifests(json.loads(text_a), json.loads(text_b))
        self.assertEqual(result["summary"]["verdict"], "完全一致")
        self.assertEqual(result["violations"], [])
        self.assertEqual(result["compliant_diffs"], [])
        self.assertEqual(result["pending_confirmations"], [])


class TestEquivalentForms(unittest.TestCase):
    def test_equivalent_writings_are_not_diffs(self):
        a = manifest(make_entry(
            "pkg-a",
            version="1.2.0",
            checksum="AB12:CD34",
            size=1024,
            path="/opt/app/bin/",
            tags=["core", "net"],
            enabled="true",
        ))
        b = manifest(make_entry(
            "pkg-a",
            version="v1.2.0",            # v 前缀等价
            checksum="ab12cd34",         # 大小写与分隔符等价
            size="1024",                 # 数字字符串与数字等价
            path="\\opt\\app\\bin",      # 反斜杠与结尾斜杠等价
            tags=["net", "core"],        # 列表顺序无关
            enabled=True,                # 布尔字符串与布尔值等价
        ))
        result = compare_manifests(a, b)
        self.assertEqual(result["summary"]["verdict"], "完全一致")
        self.assertEqual(result["violations"], [])
        self.assertEqual(result["compliant_diffs"], [])
        self.assertEqual(result["pending_confirmations"], [])

    def test_whitespace_padding_ignored(self):
        a = manifest(make_entry("pkg-a", description="  base package  "))
        b = manifest(make_entry("pkg-a", description="base package"))
        result = compare_manifests(a, b)
        self.assertEqual(result["summary"]["verdict"], "完全一致")


class TestMissingCriticalFields(unittest.TestCase):
    def test_missing_checksum_reported_separately(self):
        entry = make_entry("pkg-a")
        del entry["checksum"]
        a = manifest(make_entry("pkg-a"), make_entry("pkg-b", description="x"))
        b = manifest(entry, make_entry("pkg-b", description="y"))
        result = compare_manifests(a, b)

        missing = result["missing_critical_fields"]
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["category"], CAT_MISSING_CRITICAL)
        self.assertEqual(missing[0]["missing_fields"], ["checksum"])
        self.assertEqual(missing[0]["resource"]["name"], "pkg-a")
        # 不得混在普通差异里
        self.assertEqual(result["violations"], [])
        # 信息字段差异仍正常归类
        self.assertEqual(result["summary"]["compliant_diff_count"], 1)
        # 关键字段缺失导致总体不合规
        self.assertEqual(result["summary"]["verdict"], "不合规")

    def test_missing_key_field_cannot_be_matched(self):
        bad = make_entry("pkg-a")
        del bad["name"]
        a = manifest(bad)
        b = manifest(make_entry("pkg-a"))
        result = compare_manifests(a, b)
        missing = result["missing_critical_fields"]
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["side"], "a")
        self.assertEqual(missing[0]["missing_fields"], ["name"])
        # b 侧条目无法配对，按多余资源记违规
        self.assertEqual(result["violations"][0]["category"], CAT_ENTRY_EXTRA)


class TestVerdictAttribution(unittest.TestCase):
    def test_critical_field_mismatch_is_violation(self):
        a = manifest(make_entry("pkg-a", version="1.2.0"))
        b = manifest(make_entry("pkg-a", version="2.0.0"))
        result = compare_manifests(a, b)
        self.assertEqual(result["summary"]["verdict"], "不合规")
        violation = result["violations"][0]
        self.assertEqual(violation["verdict"], VERDICT_VIOLATION)
        self.assertEqual(violation["field"], "version")
        self.assertEqual(violation["value_a"], "1.2.0")
        self.assertEqual(violation["value_b"], "2.0.0")

    def test_informational_field_mismatch_is_compliant(self):
        a = manifest(make_entry("pkg-a", description="old", tags=["core"]))
        b = manifest(make_entry("pkg-a", description="new", tags=["core", "extra"]))
        result = compare_manifests(a, b)
        self.assertEqual(result["summary"]["verdict"], "合规（存在合规差异）")
        self.assertEqual(result["summary"]["compliant_diff_count"], 2)
        for diff in result["compliant_diffs"]:
            self.assertEqual(diff["verdict"], VERDICT_COMPLIANT)

    def test_unknown_field_mismatch_is_pending(self):
        a = manifest(make_entry("pkg-a", custom_field="x"))
        b = manifest(make_entry("pkg-a", custom_field="y"))
        result = compare_manifests(a, b)
        self.assertEqual(result["summary"]["verdict"], "待确认")
        pending = result["pending_confirmations"][0]
        self.assertEqual(pending["verdict"], VERDICT_PENDING)
        self.assertEqual(pending["field"], "custom_field")

    def test_entry_only_on_one_side_is_violation(self):
        a = manifest(make_entry("pkg-a"), make_entry("pkg-gone"))
        b = manifest(make_entry("pkg-a"), make_entry("pkg-new"))
        result = compare_manifests(a, b)
        categories = {v["category"] for v in result["violations"]}
        self.assertEqual(categories, {CAT_ENTRY_MISSING, CAT_ENTRY_EXTRA})
        self.assertEqual(result["summary"]["verdict"], "不合规")

    def test_field_present_on_one_side_only(self):
        a = manifest(make_entry("pkg-a", source="repo-1"))
        b = manifest(make_entry("pkg-a"))
        result = compare_manifests(a, b)
        self.assertEqual(result["summary"]["compliant_diff_count"], 1)
        diff = result["compliant_diffs"][0]
        self.assertEqual(diff["field"], "source")
        self.assertIsNone(diff["value_b"])


if __name__ == "__main__":
    unittest.main()
