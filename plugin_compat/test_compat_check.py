"""Self-tests for the plugin compatibility checker (stdlib unittest only)."""

import copy
import unittest

from compat_check import (
    BACKWARD_COMPATIBLE,
    COMPATIBLE,
    INCOMPATIBLE,
    check_compatibility,
)


def base_manifest(version="1.0.0"):
    return {
        "name": "imagekit",
        "version": version,
        "functions": {
            "resize_image": {
                "params": [
                    {"name": "path", "kind": "positional_or_keyword",
                     "required": True},
                    {"name": "width", "kind": "positional_or_keyword",
                     "required": False, "default": 800},
                    {"name": "format", "kind": "keyword_only",
                     "required": False, "default": "png"},
                ],
                "returns": "Image",
                "errors": {
                    "file_missing": "FileNotFoundError",
                    "bad_dimension": "ValueError",
                },
            },
            "save_image": {
                "params": [
                    {"name": "image", "kind": "positional_or_keyword",
                     "required": True},
                ],
                "returns": "None",
                "errors": {"disk_full": "IOError"},
            },
        },
    }


class TestIdentical(unittest.TestCase):
    def test_same_interface_is_compatible(self):
        old, new = base_manifest("1.0.0"), base_manifest("2.0.0")
        result = check_compatibility(old, new)
        self.assertEqual(result.verdict, COMPATIBLE)
        self.assertEqual(result.findings, ())

    def test_version_number_alone_means_nothing(self):
        # A major version bump with identical interfaces is compatible.
        old, new = base_manifest("1.0.0"), base_manifest("9.9.9")
        self.assertEqual(check_compatibility(old, new).verdict, COMPATIBLE)


class TestBackwardCompatible(unittest.TestCase):
    def test_new_optional_parameter(self):
        old, new = base_manifest(), base_manifest("1.1.0")
        new["functions"]["resize_image"]["params"].append(
            {"name": "quality", "kind": "keyword_only", "required": False,
             "default": 90})
        result = check_compatibility(old, new)
        self.assertEqual(result.verdict, BACKWARD_COMPATIBLE)
        self.assertEqual(result.incompatibilities, ())
        codes = {f.code for f in result.backward_compatible_changes}
        self.assertEqual(codes, {"PARAM_ADDED_OPTIONAL"})

    def test_new_function(self):
        old, new = base_manifest(), base_manifest("1.1.0")
        new["functions"]["thumbnail"] = {
            "params": [{"name": "path", "kind": "positional_or_keyword",
                        "required": True}],
            "returns": "Image", "errors": {}}
        result = check_compatibility(old, new)
        self.assertEqual(result.verdict, BACKWARD_COMPATIBLE)
        self.assertEqual(result.findings[0].code, "FUNCTION_ADDED")

    def test_required_becomes_optional(self):
        old, new = base_manifest(), base_manifest("1.1.0")
        new["functions"]["resize_image"]["params"][0] = {
            "name": "path", "kind": "positional_or_keyword",
            "required": False, "default": "input.png"}
        result = check_compatibility(old, new)
        self.assertEqual(result.verdict, BACKWARD_COMPATIBLE)


class TestDefaultValueChange(unittest.TestCase):
    """Samples that change ONLY a default value must be incompatible."""

    def _sample(self, old_default, new_default):
        old, new = base_manifest(), base_manifest("1.0.1")
        for m, val in ((old, old_default), (new, new_default)):
            m["functions"]["resize_image"]["params"][1]["default"] = val
        return old, new

    def test_default_value_change_is_incompatible(self):
        old, new = self._sample(800, 1024)
        result = check_compatibility(old, new)
        self.assertEqual(result.verdict, INCOMPATIBLE)
        self.assertEqual(len(result.incompatibilities), 1)
        f = result.incompatibilities[0]
        self.assertEqual(f.code, "DEFAULT_CHANGED")
        self.assertIn("800", f.detail)
        self.assertIn("1024", f.detail)

    def test_default_type_change_is_incompatible(self):
        old, new = self._sample(800, "auto")
        self.assertEqual(check_compatibility(old, new).verdict, INCOMPATIBLE)

    def test_default_none_to_value_is_incompatible(self):
        old, new = self._sample(None, 800)
        self.assertEqual(check_compatibility(old, new).verdict, INCOMPATIBLE)


class TestErrorSemanticsChange(unittest.TestCase):
    """Samples that change ONLY error semantics must be incompatible."""

    def test_error_name_changed(self):
        old, new = base_manifest(), base_manifest("1.0.1")
        new["functions"]["resize_image"]["errors"]["bad_dimension"] = \
            "TypeError"
        result = check_compatibility(old, new)
        self.assertEqual(result.verdict, INCOMPATIBLE)
        codes = {f.code for f in result.incompatibilities}
        self.assertEqual(codes, {"ERROR_NAME_CHANGED"})

    def test_error_removed(self):
        old, new = base_manifest(), base_manifest("1.0.1")
        del new["functions"]["resize_image"]["errors"]["file_missing"]
        result = check_compatibility(old, new)
        self.assertEqual(result.verdict, INCOMPATIBLE)
        self.assertEqual(result.incompatibilities[0].code, "ERROR_REMOVED")

    def test_error_added(self):
        old, new = base_manifest(), base_manifest("1.0.1")
        new["functions"]["save_image"]["errors"]["readonly"] = \
            "PermissionError"
        result = check_compatibility(old, new)
        self.assertEqual(result.verdict, INCOMPATIBLE)
        self.assertEqual(result.incompatibilities[0].code, "ERROR_ADDED")


class TestOtherIncompatible(unittest.TestCase):
    def test_function_removed(self):
        old, new = base_manifest(), base_manifest("2.0.0")
        del new["functions"]["save_image"]
        result = check_compatibility(old, new)
        self.assertEqual(result.verdict, INCOMPATIBLE)
        self.assertEqual(result.incompatibilities[0].code, "FUNCTION_REMOVED")

    def test_param_removed(self):
        old, new = base_manifest(), base_manifest("2.0.0")
        del new["functions"]["resize_image"]["params"][2]
        result = check_compatibility(old, new)
        self.assertEqual(result.verdict, INCOMPATIBLE)
        self.assertEqual(result.incompatibilities[0].code, "PARAM_REMOVED")

    def test_new_required_param(self):
        old, new = base_manifest(), base_manifest("2.0.0")
        new["functions"]["resize_image"]["params"].append(
            {"name": "mode", "kind": "positional_or_keyword",
             "required": True})
        result = check_compatibility(old, new)
        self.assertEqual(result.verdict, INCOMPATIBLE)
        self.assertEqual(result.incompatibilities[0].code,
                         "PARAM_ADDED_REQUIRED")

    def test_param_kind_changed(self):
        old, new = base_manifest(), base_manifest("2.0.0")
        new["functions"]["resize_image"]["params"][0]["kind"] = "keyword_only"
        result = check_compatibility(old, new)
        self.assertEqual(result.verdict, INCOMPATIBLE)
        self.assertEqual(result.incompatibilities[0].code,
                         "PARAM_KIND_CHANGED")

    def test_optional_becomes_required(self):
        old, new = base_manifest(), base_manifest("2.0.0")
        p = new["functions"]["resize_image"]["params"][1]
        p["required"] = True
        del p["default"]
        result = check_compatibility(old, new)
        self.assertEqual(result.verdict, INCOMPATIBLE)
        self.assertEqual(result.incompatibilities[0].code,
                         "REQUIRED_FLAG_CHANGED")

    def test_return_type_changed(self):
        old, new = base_manifest(), base_manifest("2.0.0")
        new["functions"]["resize_image"]["returns"] = "bytes"
        result = check_compatibility(old, new)
        self.assertEqual(result.verdict, INCOMPATIBLE)
        self.assertEqual(result.incompatibilities[0].code,
                         "RETURN_TYPE_CHANGED")

    def test_incompatible_beats_backward_compatible(self):
        # One safe change + one breaking change => incompatible overall.
        old, new = base_manifest(), base_manifest("2.0.0")
        new["functions"]["resize_image"]["params"].append(
            {"name": "quality", "kind": "keyword_only", "required": False,
             "default": 90})
        new["functions"]["resize_image"]["params"][1]["default"] = 1024
        result = check_compatibility(old, new)
        self.assertEqual(result.verdict, INCOMPATIBLE)
        self.assertEqual(len(result.incompatibilities), 1)
        self.assertEqual(len(result.backward_compatible_changes), 1)


class TestDeterminism(unittest.TestCase):
    def test_repeated_checks_are_identical(self):
        old, new = base_manifest(), base_manifest("2.0.0")
        new["functions"]["resize_image"]["params"][1]["default"] = 1024
        new["functions"]["resize_image"]["errors"]["bad_dimension"] = \
            "TypeError"
        new["functions"]["extra"] = {"params": [], "returns": "int",
                                     "errors": {}}
        first = check_compatibility(old, new).as_dict()
        for _ in range(50):
            again = check_compatibility(copy.deepcopy(old),
                                        copy.deepcopy(new)).as_dict()
            self.assertEqual(first, again)

    def test_manifest_key_order_does_not_matter(self):
        old, new = base_manifest(), base_manifest("2.0.0")
        new["functions"]["resize_image"]["params"][1]["default"] = 1024
        r1 = check_compatibility(old, new).as_dict()
        shuffled_old = dict(reversed(list(old["functions"].items())))
        shuffled_new = dict(reversed(list(new["functions"].items())))
        r2 = check_compatibility({**old, "functions": shuffled_old},
                                 {**new, "functions": shuffled_new}).as_dict()
        self.assertEqual(r1, r2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
