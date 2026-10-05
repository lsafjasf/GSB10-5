#!/usr/bin/env python3
"""compat_checker 自测（标准库 unittest）。

运行: python3 test_compat_checker.py -v
"""

import copy
import os
import unittest

from compat_checker import (
    BACKWARD_COMPATIBLE,
    COMPATIBLE,
    INCOMPATIBLE,
    compare_manifests,
    load_manifest,
    report_json,
)

SAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples")


def base_manifest():
    return load_manifest(os.path.join(SAMPLES, "v1.json"))


class TestVerdicts(unittest.TestCase):
    def check(self, old_name, new_name):
        old = load_manifest(os.path.join(SAMPLES, old_name))
        new = load_manifest(os.path.join(SAMPLES, new_name))
        return compare_manifests(old, new)

    def test_identical_is_compatible(self):
        report = self.check("v1.json", "v2_same.json")
        self.assertEqual(report.verdict, COMPATIBLE)
        self.assertEqual(report.findings, [])

    def test_optional_param_added_is_backward_compatible(self):
        report = self.check("v1.json", "v2_optional.json")
        self.assertEqual(report.verdict, BACKWARD_COMPATIBLE)
        kinds = {f.kind for f in report.findings}
        self.assertEqual(kinds, {"optional_param_added"})

    def test_default_change_is_incompatible(self):
        report = self.check("v1.json", "v2_default.json")
        self.assertEqual(report.verdict, INCOMPATIBLE)
        kinds = {f.kind for f in report.incompatible_findings}
        self.assertEqual(kinds, {"default_changed"})

    def test_error_semantics_change_is_incompatible(self):
        report = self.check("v1.json", "v2_error.json")
        self.assertEqual(report.verdict, INCOMPATIBLE)
        kinds = {f.kind for f in report.incompatible_findings}
        self.assertEqual(kinds, {"error_condition_changed"})

    def test_function_removed_is_incompatible(self):
        report = self.check("v1.json", "v2_removed.json")
        self.assertEqual(report.verdict, INCOMPATIBLE)
        kinds = {f.kind for f in report.incompatible_findings}
        self.assertEqual(kinds, {"function_removed"})


class TestRules(unittest.TestCase):
    def setUp(self):
        self.old = base_manifest()

    def mutate(self, fn):
        new = copy.deepcopy(self.old)
        fn(new)
        return compare_manifests(self.old, new)

    def test_function_added_is_backward_compatible(self):
        def add(m):
            m["functions"]["thumbnail"] = {
                "params": {"image": {"type": "bytes", "required": True}},
                "returns": {"type": "bytes"},
                "raises": {},
            }
        report = self.mutate(add)
        self.assertEqual(report.verdict, BACKWARD_COMPATIBLE)

    def test_required_param_added_is_incompatible(self):
        def add(m):
            m["functions"]["resize"]["params"]["quality"] = {
                "type": "int", "required": True}
        report = self.mutate(add)
        self.assertEqual(report.verdict, INCOMPATIBLE)
        self.assertIn("required_param_added",
                      {f.kind for f in report.incompatible_findings})

    def test_param_removed_is_incompatible(self):
        def rm(m):
            del m["functions"]["resize"]["params"]["algorithm"]
        report = self.mutate(rm)
        self.assertEqual(report.verdict, INCOMPATIBLE)
        self.assertIn("param_removed",
                      {f.kind for f in report.incompatible_findings})

    def test_param_type_change_is_incompatible(self):
        def ch(m):
            m["functions"]["resize"]["params"]["width"]["type"] = "float"
        report = self.mutate(ch)
        self.assertEqual(report.verdict, INCOMPATIBLE)
        self.assertIn("param_type_changed",
                      {f.kind for f in report.incompatible_findings})

    def test_param_became_required_is_incompatible(self):
        def ch(m):
            m["functions"]["resize"]["params"]["algorithm"]["required"] = True
        report = self.mutate(ch)
        self.assertEqual(report.verdict, INCOMPATIBLE)
        self.assertIn("param_became_required",
                      {f.kind for f in report.incompatible_findings})

    def test_param_became_optional_is_backward_compatible(self):
        def ch(m):
            m["functions"]["convert"]["params"]["format"]["required"] = False
            m["functions"]["convert"]["params"]["format"]["default"] = "png"
        report = self.mutate(ch)
        self.assertEqual(report.verdict, BACKWARD_COMPATIBLE)

    def test_default_added_is_incompatible(self):
        def ch(m):
            del m["functions"]["resize"]["params"]["algorithm"]["default"]
        report = self.mutate(ch)
        self.assertEqual(report.verdict, INCOMPATIBLE)
        self.assertIn("default_changed",
                      {f.kind for f in report.incompatible_findings})

    def test_return_type_change_is_incompatible(self):
        def ch(m):
            m["functions"]["resize"]["returns"]["type"] = "str"
        report = self.mutate(ch)
        self.assertEqual(report.verdict, INCOMPATIBLE)
        self.assertIn("return_type_changed",
                      {f.kind for f in report.incompatible_findings})

    def test_error_added_is_incompatible(self):
        def ch(m):
            m["functions"]["resize"]["raises"]["MemoryError"] = "图片过大"
        report = self.mutate(ch)
        self.assertEqual(report.verdict, INCOMPATIBLE)
        self.assertIn("error_added",
                      {f.kind for f in report.incompatible_findings})

    def test_error_removed_is_incompatible(self):
        def ch(m):
            del m["functions"]["resize"]["raises"]["IOError"]
        report = self.mutate(ch)
        self.assertEqual(report.verdict, INCOMPATIBLE)
        self.assertIn("error_removed",
                      {f.kind for f in report.incompatible_findings})


class TestDeterminism(unittest.TestCase):
    def test_repeated_runs_are_identical(self):
        old = load_manifest(os.path.join(SAMPLES, "v1.json"))
        new = load_manifest(os.path.join(SAMPLES, "v2_default.json"))
        outputs = {report_json(compare_manifests(old, new)) for _ in range(50)}
        self.assertEqual(len(outputs), 1)

    def test_shuffled_input_gives_same_report(self):
        old = load_manifest(os.path.join(SAMPLES, "v1.json"))
        new = load_manifest(os.path.join(SAMPLES, "v2_default.json"))
        shuffled = copy.deepcopy(new)
        funcs = shuffled["functions"]
        shuffled["functions"] = dict(reversed(list(funcs.items())))
        resize = shuffled["functions"]["resize"]
        resize["params"] = dict(reversed(list(resize["params"].items())))
        self.assertEqual(report_json(compare_manifests(old, new)),
                         report_json(compare_manifests(old, shuffled)))


if __name__ == "__main__":
    unittest.main()
