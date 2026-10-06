#!/usr/bin/env python3
"""统一错误码库自测：覆盖无冲突、同码异义、重复码、翻译缺失/回退、已发布码改义。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from errcodes import (
    FALLBACK_CODE,
    FALLBACK_DEFAULT,
    build,
    check_stability,
    make_code,
    parse_code,
    Translator,
)
from errcodes.core import CATEGORY_NAMES
from errcodes.registry import SEVERITY_ERROR, SEVERITY_WARN


def err(code, name="N", msg="m", module="auth"):
    return module, {"code": code, "name": name, "default_message": msg}


class TestCodeRules(unittest.TestCase):
    def test_parse_and_make(self):
        self.assertEqual(make_code(1, 2, 3), "E0102003")
        self.assertEqual(parse_code("E0102003"), ("01", "02", "003"))
        self.assertIsNone(parse_code("X0102003"))
        self.assertIsNone(parse_code("E010203"))
        self.assertIsNone(parse_code("E01020030"))

    def test_categories_registered(self):
        self.assertIn("01", CATEGORY_NAMES)


class TestCleanRegistry(unittest.TestCase):
    def test_no_conflict(self):
        loaded = [
            err("E0101001", "A", "a"),
            err("E0102001", "B", "b"),
            err("E0201001", "C", "c", module="payment"),
        ]
        registry = build(loaded)
        self.assertEqual(len(registry.entries), 3)
        self.assertFalse(registry.has_errors())


class TestConflictDetection(unittest.TestCase):
    def test_duplicate_same_name(self):
        loaded = [err("E0101001", "SAME", "a"),
                  err("E0101001", "SAME", "a")]
        registry = build(loaded)
        kinds = [i.kind for i in registry.issues]
        self.assertIn("duplicate_code", kinds)
        self.assertTrue(registry.has_errors())

    def test_code_redefined_same_code_different_meaning(self):
        loaded = [
            err("E0102002", "FORBIDDEN", "no permission"),
            err("E0102002", "RISK_REJECTED", "risk", module="payment"),
        ]
        registry = build(loaded)
        issue = next(i for i in registry.issues if i.kind == "code_redefined")
        self.assertEqual(issue.severity, SEVERITY_ERROR)
        self.assertIn("FORBIDDEN", issue.message)
        self.assertIn("RISK_REJECTED", issue.message)

    def test_area_mismatch(self):
        loaded = [err("E0202001", "X", "x", module="auth")]
        registry = build(loaded)
        self.assertTrue(
            any(i.kind == "area_mismatch" and i.severity == SEVERITY_ERROR
                for i in registry.issues))

    def test_malformed_and_reserved(self):
        loaded = [err("E0104999", "FALLBACK", "f"),
                  err("ERR0001A", "BAD", "bad")]
        registry = build(loaded)
        kinds = {i.kind for i in registry.issues}
        self.assertIn("reserved_serial", kinds)
        self.assertIn("malformed_code", kinds)
        self.assertEqual(registry.issues[-1].severity, SEVERITY_ERROR)

    def test_duplicate_name_warns(self):
        loaded = [err("E0102001", "FORBIDDEN", "a"),
                  err("E0102004", "FORBIDDEN", "b")]
        registry = build(loaded)
        self.assertTrue(
            any(i.kind == "duplicate_name" and i.severity == SEVERITY_WARN
                for i in registry.issues))


class TestTranslation(unittest.TestCase):
    def setUp(self):
        self.registry = build([
            err("E0101001", "A", "default-A"),
            err("E0102001", "B", "default-B"),
        ])

    def test_coverage_and_missing(self):
        tr = Translator(self.registry, "zh-CN", {"E0101001": "参数无效"})
        report = tr.coverage()
        self.assertEqual(report["total"], 2)
        self.assertEqual(report["translated"], 1)
        self.assertEqual(report["missing_count"], 1)
        self.assertEqual(report["coverage_percent"], 50.0)
        self.assertEqual(report["missing"][0]["code"], "E0102001")

    def test_fallback_default_message(self):
        tr = Translator(self.registry, "zh-CN", {},
                        fallback=FALLBACK_DEFAULT)
        text, source = tr.translate("E0102001")
        self.assertEqual(text, "default-B")
        self.assertEqual(source, "default")

    def test_fallback_code(self):
        tr = Translator(self.registry, "zh-CN", {}, fallback=FALLBACK_CODE)
        text, source = tr.translate("E0102001")
        self.assertEqual(text, "E0102001")
        self.assertEqual(source, "code")

    def test_translated_source(self):
        tr = Translator(self.registry, "zh-CN", {"E0101001": "参数无效"})
        text, source = tr.translate("E0101001")
        self.assertEqual(text, "参数无效")
        self.assertEqual(source, "translated")

    def test_unknown_code_shows_raw_code(self):
        tr = Translator(self.registry, "zh-CN", {})
        text, source = tr.translate("E9999999")
        self.assertEqual(text, "E9999999")
        self.assertEqual(source, "missing")


class TestStability(unittest.TestCase):
    def setUp(self):
        self.snapshot = {"version": "v1", "codes": {
            "E0101001": {"name": "A", "default_message": "a", "module": "auth"},
            "E0102001": {"name": "B", "default_message": "b", "module": "auth"},
        }}

    def test_published_code_repurposed_is_detected(self):
        registry = build([
            err("E0101001", "A", "a"),
            err("E0102001", "B_NEW_MEANING", "b2"),
            err("E0103001", "C", "c"),
        ])
        result = check_stability(registry, self.snapshot)
        self.assertTrue(result["has_breaking_changes"])
        self.assertEqual(result["repurposed_count"], 1)
        item = result["repurposed"][0]
        self.assertEqual(item["code"], "E0102001")
        self.assertEqual(item["old_name"], "B")
        self.assertEqual(item["new_name"], "B_NEW_MEANING")
        self.assertEqual(result["added_count"], 1)

    def test_message_drift_without_rename_is_warn_data(self):
        registry = build([
            err("E0101001", "A", "a"),
            err("E0102001", "B", "message changed"),
        ])
        result = check_stability(registry, self.snapshot)
        self.assertFalse(result["has_breaking_changes"])
        self.assertEqual(result["message_drift_count"], 1)

    def test_removed_code_detected(self):
        registry = build([err("E0101001", "A", "a")])
        result = check_stability(registry, self.snapshot)
        self.assertEqual(result["removed_count"], 1)
        self.assertEqual(result["removed"][0]["code"], "E0102001")

    def test_unchanged_is_clean(self):
        registry = build([err("E0101001", "A", "a"),
                          err("E0102001", "B", "b")])
        result = check_stability(registry, self.snapshot)
        self.assertFalse(result["has_breaking_changes"])
        self.assertEqual(result["repurposed_count"], 0)
        self.assertEqual(result["message_drift_count"], 0)
        self.assertEqual(result["removed_count"], 0)
        self.assertEqual(result["added_count"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
