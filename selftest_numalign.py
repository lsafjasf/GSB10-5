"""Self-tests, alignment demo, and 100,000-row benchmark."""

from __future__ import annotations

import gc
import resource
import time
import tracemalloc
import unittest

from numalign import (
    NumericFormat,
    align_columns,
    clear_caches,
    display_width,
    grapheme_clusters,
    normalize_number,
    truncate_display,
    verify_column_starts,
)


class WidthTests(unittest.TestCase):
    def test_half_width_digits(self) -> None:
        self.assertEqual(display_width("12345"), 5)
        self.assertEqual(display_width("-1,234.50"), 9)

    def test_full_width_digits(self) -> None:
        self.assertEqual(display_width("１２３"), 6)
        self.assertEqual(display_width("汉字１２３"), 10)

    def test_combining_sequence(self) -> None:
        text = "e\u0301"
        self.assertEqual(display_width(text), 1)
        self.assertEqual(grapheme_clusters(text), [text])
        self.assertEqual(display_width("क\u0902"), 1)
        self.assertEqual(display_width("क\u093f"), 2)

    def test_zero_width_characters(self) -> None:
        self.assertEqual(display_width("a\u200bb"), 2)
        self.assertEqual(display_width("a\u200cb"), 2)
        self.assertEqual(display_width("a\ufeffb"), 2)

    def test_extreme_width_clusters(self) -> None:
        self.assertEqual(display_width("𠮷"), 2)
        self.assertEqual(display_width("🧑\u200d🚀"), 2)
        self.assertEqual(display_width("🇨🇳"), 2)
        self.assertEqual(display_width("1\ufe0f\u20e3"), 2)

    def test_safe_truncation_never_splits_a_cluster(self) -> None:
        combined = "e\u0301"
        self.assertEqual(truncate_display(combined + "x", 1), combined)

        zwj = "🧑\u200d🚀"
        self.assertEqual(truncate_display(zwj + "x", 1), "")
        self.assertEqual(truncate_display(zwj + "x", 2), zwj)

        keycap = "1\ufe0f\u20e3"
        self.assertEqual(truncate_display(keycap + "x", 1), "")

        spacing_mark = "क\u093f"
        self.assertEqual(truncate_display(spacing_mark + "x", 1), "")
        self.assertEqual(truncate_display(spacing_mark + "x", 2), spacing_mark)


class NumericTests(unittest.TestCase):
    def test_canonical_numeric_format(self) -> None:
        cases = {
            "1234.5": "1,234.50",
            "1,234.500": "1,234.50",
            "−987.6": "-987.60",
            "１２３４．５": "1,234.50",
            "1.234567e9": "1.23E+09",
            "0.00001234": "1.23E-05",
            "2.5E-4": "2.50E-04",
            "-0.00": "0.00",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_number(raw), expected)

    def test_numeric_format_without_thousands(self) -> None:
        fmt = NumericFormat(use_thousands=False)
        self.assertEqual(normalize_number("1,234.5", fmt), "1234.50")


def print_width_rules() -> None:
    print("== 显示宽度规则 ==")
    print("1. 先切分为终端原子簇：组合附加符、emoji 修饰符和 variation selector 贴住前一字符。")
    print("2. ZWJ(U+200D) 把相邻代码点连成一个原子簇；国家/地区旗帜按两个 regional indicator 成簇。")
    print("3. East_Asian_Width 为 W 或 F 的字符占 2 列，其他普通可打印字符占 1 列。")
    print("4. Mn/Me/Cf、ZWJ/ZWNJ、零宽空格和 BOM 占 0 列；Mc 占位组合符占 1 列并留在同簇。")
    print("5. ZWJ emoji 序列、旗帜和键帽序列整体占 2 列；截断时放不下整个簇就不放入。")
    print("6. Tab 固定占 4 列，其他 C0/C1 控制符占 0 列。")
    print()


def print_numeric_comparison() -> None:
    samples = [
        "1234.5",
        "1,234.500",
        "−987.6",
        "１２３４．５",
        "1.234567e9",
        "0.00001234",
        "2.5E-4",
        "-0.00",
    ]
    print("== 数字本体统一前/后 ==")
    print("before                 after")
    for raw in samples:
        print(f"{raw:<22} {normalize_number(raw)}")
    print()


def print_alignment_demo() -> None:
    rows = [
        ["名称", "数量", "金额", "状态"],
        ["alpha", "1234.5", "1,234.500", "OK"],
        ["汉字１２３", "１２３４．５", "−987.6", "全角"],
        ["e\u0301clair", "1.234567e9", "1,234.5", "组合序列"],
        ["🧑\u200d🚀", "0.00001234", "2.5E-4", "ZWJ emoji"],
        ["🇨🇳", "1,234.500", "1234567890123", "旗帜"],
        ["零宽a\u200bb", "-0.00", "8", "不可见字符"],
    ]

    table = align_columns(rows)
    print("== 对齐结果 ==")
    for line in table.lines:
        print(line)
    print()

    print("== 逐行逐列起始位置断言 ==")
    for row, column, expected in verify_column_starts(table):
        print(f"ASSERT OK row={row} col={column} start={expected}")
    print(f"TOTAL ASSERTIONS={len(table.cells) * len(table.column_starts)}")
    print()


def run_benchmark() -> None:
    row_count = 100_000
    names = ["alpha", "汉字１２３", "e\u0301clair", "🧑\u200d🚀", "🇨🇳", "a\u200bb"]
    quantities = [
        "1234.5",
        "１２３４．５",
        "1.234567e9",
        "0.00001234",
        "1,234.500",
        "-0.00",
    ]
    amounts = [
        "1,234.500",
        "−987.6",
        "1,234.5",
        "2.5E-4",
        "1234567890123",
        "8",
    ]
    statuses = ["OK", "全角", "组合序列", "极宽", "旗帜", "零宽"]

    rows = [
        [names[index % len(names)], quantities[index % 6], amounts[index % 6], statuses[index % 6]]
        for index in range(row_count)
    ]

    clear_caches()
    start = time.perf_counter()
    table = align_columns(rows)
    elapsed = time.perf_counter() - start
    del table

    clear_caches()
    gc.collect()
    tracemalloc.start()
    memory_start = time.perf_counter()
    table = align_columns(rows)
    memory_elapsed = time.perf_counter() - memory_start
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    print("== 100,000 行基准 ==")
    print(f"rows={row_count}")
    print(f"columns={len(table.column_starts)}")
    print(f"elapsed_seconds={elapsed:.6f}")
    print(f"tracemalloc_elapsed_seconds={memory_elapsed:.6f}")
    print(f"tracemalloc_current_mib={current / 1024 / 1024:.2f}")
    print(f"tracemalloc_peak_mib={peak / 1024 / 1024:.2f}")
    print(f"process_max_rss_mib={rss_kib / 1024:.2f}")


def run() -> None:
    loader = unittest.defaultTestLoader
    suite = loader.loadTestsFromName(__name__)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)

    print()
    print_width_rules()
    print_numeric_comparison()
    print_alignment_demo()
    run_benchmark()


if __name__ == "__main__":
    run()
