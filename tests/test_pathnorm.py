"""Self-tests for pathnorm (stdlib unittest). Run: python -m unittest -v"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pathnorm as pn  # noqa: E402


class NormalizePosix(unittest.TestCase):
    def test_separator_only_differences_are_not_separators(self):
        # On POSIX '\\' is a valid filename character, not a separator.
        self.assertEqual(pn.normalize("a/b", "posix"), "a/b")
        self.assertEqual(pn.normalize("a//b", "posix"), "a/b")
        self.assertEqual(pn.normalize("a\\\\b", "posix"), "a\\\\b")

    def test_collapse_and_dot_segments(self):
        cases = {
            "/a//b/./c": "/a/b/c",
            "/a/b/../c": "/a/c",
            "/a/../../b": "/b",           # .. at root dropped
            "a/../b": "b",
            "../a/b": "../a/b",           # leading .. preserved
            "a/./b/": "a/b",
            "/": "/",
            ".": "",
        }
        for raw, want in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(pn.normalize(raw, "posix"), want)

    def test_case_preserved_when_sensitive(self):
        self.assertEqual(pn.normalize("/Foo/BAR", "posix"), "/Foo/BAR")


class NormalizeWindows(unittest.TestCase):
    def test_pure_separator_difference(self):
        # Forward vs back slashes, plus duplicate separators.
        pairs = [
            (r"c:\Projects\app/config.yaml", r"C:/Projects/app/config.yaml"),
            (r"C:\a\b\c", r"C:/a//b/c"),
            (r"\\server\share\dir\f", r"//server/share/dir/f"),
        ]
        for a, b in pairs:
            with self.subTest(a=a, b=b):
                self.assertEqual(pn.normalize(a, "windows"),
                                 pn.normalize(b, "windows"))

    def test_mixed_separators(self):
        self.assertEqual(
            pn.normalize(r"C:\a/b\c/d", "windows"),
            r"C:\a\b\c\d",
        )

    def test_dot_segments_and_relative(self):
        self.assertEqual(pn.normalize(r"C:\a\..\b", "windows"), r"C:\b")
        self.assertEqual(pn.normalize(r"a\.\b", "windows"), r"a\b")
        self.assertEqual(pn.normalize(r"..\a\b", "windows"), r"..\a\b")

    def test_drive_letter_uppercased(self):
        self.assertEqual(pn.normalize(r"d:\x", "windows"), r"D:\x")

    def test_unc_preserved(self):
        self.assertEqual(
            pn.normalize(r"\\srv\share\a\b", "windows"),
            r"\\srv\share\a\b",
        )

    def test_extended_prefix_and_limit(self):
        long_tail = "\\".join(["d" * 50] * 10)  # 509 chars, short components
        got = pn.normalize("\\\\?\\C:\\" + long_tail, "windows")
        self.assertTrue(got.startswith("\\\\?\\C:\\"))
        self.assertEqual(len(got), 4 + 3 + 509)

    def test_trailing_dots_and_spaces_stripped(self):
        self.assertEqual(pn.normalize(r"C:\a\file.", "windows"), r"C:\a\file")
        self.assertEqual(pn.normalize(r"C:\a\file  ", "windows"), r"C:\a\file")

    def test_backslash_is_separator_unlike_posix(self):
        self.assertEqual(
            pn.normalize("a/b\\c", "windows"), r"a\b\c"
        )
        self.assertEqual(
            pn.normalize("a/b\\c", "posix"), "a/b\\c"
        )


class CaseSensitivity(unittest.TestCase):
    """Same FS location must compare equal on a case-insensitive FS."""

    PAIRS = [
        ("/Users/Alice/Report.TXT", "/users/alice/report.txt"),
        (r"C:\Projects\APP.TXT", r"c:\projects\app.txt"),
    ]

    def test_case_sensitive_fs_distinguishes(self):
        for a, b in self.PAIRS:
            style = "windows" if "\\" in a or ":" in a else "posix"
            self.assertFalse(
                pn.equivalent(a, b, style, case_sensitive=True), (a, b)
            )

    def test_case_insensitive_fs_identifies(self):
        for a, b in self.PAIRS:
            style = "windows" if "\\" in a or ":" in a else "posix"
            self.assertTrue(
                pn.equivalent(a, b, style, case_sensitive=False), (a, b)
            )

    def test_casefold_beyond_simple_lower(self):
        # 'SS' vs German sharp s under Unicode casefold (case-insensitive FS).
        self.assertTrue(
            pn.equivalent("/Stra\u00dfe", "/strasse", "posix",
                          case_sensitive=False)
        )
        self.assertFalse(
            pn.equivalent("/Stra\u00dfe", "/strasse", "posix",
                          case_sensitive=True)
        )


class ReservedNames(unittest.TestCase):
    BAD = ["CON", "con", "NUL", "aux", "Com1", "LPT9", "CON.txt",
           "nul.anything"]

    def test_reserved_rejected_on_windows(self):
        for name in self.BAD:
            with self.subTest(name=name):
                with self.assertRaises(pn.ReservedNameError):
                    pn.normalize(r"C:\work\\" + name, "windows")

    def test_reserved_ok_on_posix(self):
        for name in ["CON", "NUL", "LPT9"]:
            self.assertEqual(
                pn.normalize("/work/" + name, "posix"),
                "/work/" + name,
            )

    def test_nonreserved_similar_names_ok(self):
        self.assertEqual(
            pn.normalize(r"C:\work\config", "windows"),
            r"C:\work\config",
        )

    def test_strict_false_does_not_raise(self):
        got = pn.normalize(r"C:\work\CON", "windows", strict=False)
        self.assertEqual(got, r"C:\work\CON")


class LengthLimits(unittest.TestCase):
    def test_windows_max_path(self):
        # 259 chars of short components + separator -> exceeds 260.
        path = "C:\\" + "\\".join(["a" * 50] * 6)
        with self.assertRaises(pn.PathTooLongError) as ctx:
            pn.normalize(path, "windows")
        self.assertEqual(ctx.exception.limit, pn.WINDOWS_MAX_PATH)

    def test_posix_path_max(self):
        path = "/" + "/".join(["a" * 100] * 41)  # 4141 chars
        with self.assertRaises(pn.PathTooLongError):
            pn.normalize(path, "posix")

    def test_component_name_max(self):
        path = "/x/" + "a" * (pn.NAME_MAX + 1)
        with self.assertRaises(pn.ComponentTooLongError):
            pn.normalize(path, "posix")

    def test_no_silent_truncation_length_preserved(self):
        comp = "b" * (pn.NAME_MAX)
        got = pn.normalize("/a/" + comp, "posix")
        self.assertTrue(got.endswith(comp))
        self.assertEqual(len(got), 2 + 1 + pn.NAME_MAX)

    def test_check_report_contains_error(self):
        rep = pn.check("C:\\" + "\\".join(["x" * 50] * 6), "windows")
        self.assertFalse(rep["ok"])
        self.assertIn("PathTooLongError", rep["error"])


class EquivalenceTable(unittest.TestCase):
    def test_documentation_corpus(self):
        # Mirrors the equivalence table in RULES.md.
        rows = [
            # a, b, style, case_sensitive, expected
            ("/etc/../etc/passwd", "/etc/passwd", "posix", True, True),
            ("a//b", "a/b", "posix", True, True),
            ("/A/B", "/a/b", "posix", True, False),
            ("/A/B", "/a/b", "posix", False, True),
            (r"C:\A\B", r"c:/a/b", "windows", True, False),
            (r"C:\A\B", r"c:/a/b", "windows", False, True),
            (r"\\s\share\f", r"//s/share/f", "windows", True, True),
            (r"C:\x\.", r"c:/x", "windows", False, True),
        ]
        for a, b, style, cs, expected in rows:
            with self.subTest(a=a, b=b):
                self.assertIs(
                    pn.equivalent(a, b, style, case_sensitive=cs), expected
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
