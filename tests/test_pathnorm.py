"""pathnorm 自测：unittest，仅标准库。
运行: python3 tests/test_pathnorm.py -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pathnorm import (
    EmptyPathError,
    PathTooLongError,
    ReservedNameError,
    equivalent,
    normalize,
)


class TestSeparators(unittest.TestCase):
    """纯分隔符差异 / 混用分隔符。"""

    def test_backslash_vs_forward(self):
        n = normalize(r"C:\Users\Alice\file.txt", style="windows")
        self.assertEqual(n.display, "C:/Users/Alice/file.txt")

    def test_mixed_separators(self):
        a = normalize(r"C:\Users/Alice\Documents\file.txt")
        b = normalize("C:/Users/Alice/Documents/file.txt")
        self.assertEqual(a.key, b.key)

    def test_duplicate_separators_collapse(self):
        n = normalize("a//b///c", style="posix")
        self.assertEqual(n.display, "a/b/c")

    def test_trailing_separator_removed(self):
        self.assertEqual(normalize("/a/b/", style="posix").display, "/a/b")
        self.assertEqual(normalize("C:/a/b/", style="windows").display, "C:/a/b")

    def test_root_kept(self):
        self.assertEqual(normalize("/", style="posix").display, "/")
        self.assertEqual(normalize("C:\\", style="windows").display, "C:/")

    def test_unc_preserved(self):
        n = normalize(r"\\server\share\dir\f.txt", style="windows")
        self.assertEqual(n.display, "//server/share/dir/f.txt")
        self.assertTrue(n.is_absolute)


class TestRelativeResolution(unittest.TestCase):
    def test_dot_removed(self):
        self.assertEqual(normalize("./a/./b", style="posix").display, "a/b")

    def test_dotdot_cancels(self):
        self.assertEqual(normalize("a/b/../c", style="posix").display, "a/c")

    def test_dotdot_leading_kept_for_relative(self):
        self.assertEqual(normalize("../a", style="posix").display, "../a")

    def test_dotdot_above_root_dropped(self):
        self.assertEqual(normalize("/../a", style="posix").display, "/a")

    def test_relative_equivalence(self):
        self.assertTrue(equivalent("a/./b/../c", "a/c", style="posix",
                                   case_sensitive=True))


class TestCaseHandling(unittest.TestCase):
    """大小写敏感 / 不敏感对照。"""

    def test_insensitive_equal(self):
        self.assertTrue(equivalent("C:/Users/ALICE/f.TXT", "c:/users/alice/f.txt",
                                   style="windows", case_sensitive=False))

    def test_sensitive_not_equal(self):
        self.assertFalse(equivalent("/home/Alice", "/home/alice",
                                    style="posix", case_sensitive=True))

    def test_display_preserves_case(self):
        n = normalize("C:/Users/Alice", style="windows", case_sensitive=False)
        self.assertEqual(n.display, "C:/Users/Alice")
        self.assertEqual(n.key, "c:/users/alice")

    def test_drive_letter_uppercased(self):
        self.assertEqual(normalize("c:/a", style="windows").drive, "C:")


class TestReservedNames(unittest.TestCase):
    """Windows 保留设备名：windows 语义报错，posix 语义合法。"""

    def test_reserved_bare(self):
        for name in ("CON", "con", "PRN", "AUX", "NUL", "COM1", "com9", "LPT3"):
            with self.assertRaises(ReservedNameError, msg=name):
                normalize(f"C:/dir/{name}", style="windows")

    def test_reserved_with_extension(self):
        with self.assertRaises(ReservedNameError):
            normalize("C:/dir/con.txt", style="windows")

    def test_reserved_prefix_ok(self):
        # 仅前缀相同不算保留名
        n = normalize("C:/dir/conversation.txt", style="windows")
        self.assertEqual(n.display, "C:/dir/conversation.txt")

    def test_reserved_allowed_on_posix(self):
        n = normalize("/tmp/CON", style="posix")
        self.assertEqual(n.display, "/tmp/CON")


class TestLongPaths(unittest.TestCase):
    """超长路径：确定性报错，不截断。"""

    def test_windows_too_long(self):
        long_path = "C:/" + "a" * 300
        with self.assertRaises(PathTooLongError):
            normalize(long_path, style="windows")

    def test_windows_extended_prefix_allows_long(self):
        long_path = "\\\\?\\C:\\" + "a" * 300
        n = normalize(long_path, style="windows")
        self.assertTrue(n.display.startswith("C:/"))

    def test_windows_extended_still_has_limit(self):
        too_long = "\\\\?\\C:\\" + "a" * 33000
        with self.assertRaises(PathTooLongError):
            normalize(too_long, style="windows")

    def test_posix_too_long(self):
        with self.assertRaises(PathTooLongError):
            normalize("/" + "a" * 5000, style="posix")

    def test_no_silent_truncation(self):
        # 报错信息中必须包含长度与上限，而不是返回截断结果
        try:
            normalize("C:/" + "x" * 300, style="windows")
        except PathTooLongError as e:
            self.assertIn("260", str(e))
        else:
            self.fail("应当抛出 PathTooLongError")


class TestEquivalenceTable(unittest.TestCase):
    """等价性判定数据：与 RULES.md 中的表一一对应。"""

    CASES = [
        # (路径A, 路径B, style, case_sensitive, 期望等价)
        (r"C:\Users\Alice\f.txt", "C:/Users/Alice/f.txt", "windows", False, True),   # 纯分隔符
        (r"C:\Users/Alice\f.txt", "C:/Users/Alice/f.txt", "windows", False, True),   # 混用分隔符
        ("C:/Users/ALICE/f.TXT", "c:/users/alice/f.txt", "windows", False, True),    # 大小写(不敏感)
        ("/home/Alice", "/home/alice", "posix", True, False),                        # 大小写(敏感)
        ("/home/Alice", "/home/alice", "posix", False, True),                        # posix+不敏感
        ("a/./b/../c", "a/c", "posix", True, True),                                  # 相对分量
        ("C:/a/b/", "C:/a/b", "windows", False, True),                               # 末尾分隔符
        (r"\\srv\share\d", "//srv/share/d", "windows", False, True),                 # UNC
        ("/x//y///z", "/x/y/z", "posix", True, True),                                # 重复分隔符
        ("C:/a/b", "C:/a/c", "windows", False, False),                               # 不同路径
        ("../a", "a", "posix", True, False),                                         # 相对 vs 不同
    ]

    def test_table(self):
        for a, b, style, cs, expected in self.CASES:
            with self.subTest(a=a, b=b, style=style, cs=cs):
                self.assertEqual(equivalent(a, b, style=style, case_sensitive=cs),
                                 expected)


class TestEdgeCases(unittest.TestCase):
    def test_empty(self):
        with self.assertRaises(EmptyPathError):
            normalize("")

    def test_nul_char(self):
        with self.assertRaises(ValueError):
            normalize("a\x00b")

    def test_single_dot(self):
        self.assertEqual(normalize(".", style="posix").display, ".")

    def test_immutable_result(self):
        n = normalize("a/b")
        with self.assertRaises(AttributeError):
            n.display = "x"  # type: ignore


if __name__ == "__main__":
    unittest.main(verbosity=2)
