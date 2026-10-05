#!/usr/bin/env python3
"""Self-test / report generator for the plural selection library.

Sections:
  1. Edge-case category tables (zero / one / decimals / negatives / huge)
  2. Formatting linkage: decimals, thousands separator, ordinals
  3. Cross-check A: every CLDR sample value -> library category
  4. Cross-check B: dense grid, hand-written rules vs parsed CLDR rules
  5. Benchmark: 1,000,000 (number, locale) selections

Run:  python3 selftest.py
"""
import platform
import random
import sys
import time

import plural
from cldr_data import (CARDINAL_RULES, CARDINAL_SAMPLE_TEXT,
                       ORDINAL_RULES, ORDINAL_SAMPLE_TEXT)
from cldr_parser import evaluate, expand_samples

LOCALES = ["en", "ru", "ar", "fr", "ja"]
NAMES = {"en": "英语", "ru": "俄语", "ar": "阿拉伯语", "fr": "法语", "ja": "日语"}


def edge_case_tables():
    print("## 1. 边界取值类别表（基数）\n")
    cases = [
        ("0", None), ("1", None), ("2", None),
        ("0.5", None), ("1.0", None), ("1.00", None), ("1.5", None),
        ("2.0", None), ("2.5", None),
        ("-1", None), ("-2", None), ("-0.5", None), ("-1.5", None),
        ("11", None), ("12", None), ("14", None),
        ("21", None), ("22", None), ("25", None),
        ("101", None), ("111", None), ("1000000", None),
        (str(10 ** 30), None),
        ("123456789012345678901234567890", None),
    ]
    header = "| 输入 | " + " | ".join(LOCALES) + " |"
    sep = "|" + "---|" * (len(LOCALES) + 1)
    print(header)
    print(sep)
    for value, dec in cases:
        row = [f"`{value}`"]
        for loc in LOCALES:
            row.append(plural.plural_category(loc, value, decimals=dec))
        print("| " + " | ".join(row) + " |")
    print()
    print("说明：负数按 CLDR 规定取绝对值后判定；`1.0`/`1.00` 以字符串传入，"
          "保留可见小数位（v=1 / v=2），与 `1`（v=0）类别可能不同。\n")

    print("### 序数（ordinal）边界表\n")
    ord_cases = [0, 1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 101, 102, 103, 111]
    print("| 输入 | en | en 后缀 | fr |")
    print("|---|---|---|---|")
    for value in ord_cases:
        cat_en = plural.plural_category("en", value, ordinal=True)
        cat_fr = plural.plural_category("fr", value, ordinal=True)
        suffix = plural.ORDINAL_SUFFIX_EN[cat_en]
        print(f"| {value} | {cat_en} | {value}{suffix} | {cat_fr} |")
    print()


def formatting_linkage():
    print("## 2. 数字格式化与类别判定联动对照\n")
    print("同一数值、不同格式化参数 → 不同类别（类别取自格式化后的串）：\n")
    rows = [
        ("en", "1", None, "1 位整数"),
        ("en", "1", 1, "保留 1 位小数 → v=1"),
        ("en", "1", 2, "保留 2 位小数 → v=2"),
        ("ru", "1", None, "v=0 → one"),
        ("ru", "1", 1, "v=1 → other"),
        ("ru", "1.04", 1, "四舍五入为 1.0 → other"),
        ("ru", "1.04", 0, "四舍五入为 1 → one"),
        ("ar", "1", None, "n=1 → one"),
        ("ar", "1", 1, "n=1（ar 用 n 判定）→ one"),
        ("ar", "1", 2, "n=1 → one"),
        ("fr", "1000000", None, "i%10^6=0 且 v=0 → many"),
        ("fr", "1000000", 1, "v=1 → other"),
        ("fr", "1500000", None, "i%10^6≠0 → other"),
        ("en", "1234567.891", 2, "千分位 1,234,567.89"),
        ("ru", "1234567.891", 2, "千分位不影响操作数"),
    ]
    print("| 语言 | 输入 | decimals | 格式化结果 | 类别 | 说明 |")
    print("|---|---|---|---|---|---|")
    for loc, value, dec, note in rows:
        formatted = plural.format_number(value, decimals=dec)
        cat = plural.plural_category(loc, value, decimals=dec)
        dec_show = "—" if dec is None else dec
        print(f"| {loc} | `{value}` | {dec_show} | `{formatted}` | **{cat}** | {note} |")
    print()
    print("序数联动（en 后缀选择同样走类别判定）：\n")
    print("| 输入 | 类别 | 渲染 |")
    print("|---|---|---|")
    for value in [1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 101, 111]:
        cat = plural.plural_category("en", value, ordinal=True)
        print(f"| {value} | {cat} | {value}{plural.ORDINAL_SUFFIX_EN[cat]} |")
    print()


def cross_check_samples():
    print("## 3. 对拍 A：CLDR 官方样本逐条对拍\n")
    print("数据源：CLDR 48 `plurals.json` / `ordinals.json` 中每条规则携带的 "
          "`@integer` / `@decimal` 样本（区间样本取端点+中点，"
          "紧凑记数法样本如 `1c6` 跳过）。\n")
    total = fail = 0
    print("| 语言 | 类型 | 样本数 | 通过 | 失败 |")
    print("|---|---|---|---|---|")
    for loc in LOCALES:
        for kind, rules, samples in (
                ("cardinal", CARDINAL_RULES, CARDINAL_SAMPLE_TEXT),
                ("ordinal", ORDINAL_RULES, ORDINAL_SAMPLE_TEXT)):
            loc_total = loc_fail = 0
            failures = []
            for count, sample_text in samples[loc].items():
                for value in expand_samples(sample_text):
                    loc_total += 1
                    got = plural.plural_category(
                        loc, value, ordinal=(kind == "ordinal"))
                    if got != count:
                        loc_fail += 1
                        failures.append((value, count, got))
            total += loc_total
            fail += loc_fail
            print(f"| {loc} | {kind} | {loc_total} | "
                  f"{loc_total - loc_fail} | {loc_fail} |")
            for value, want, got in failures[:10]:
                print(f"  - 失败: {loc}/{kind} `{value}` 期望 {want} 实得 {got}")
    print(f"\n合计 {total} 条样本，失败 {fail} 条。"
          + ("✅ 全部通过\n" if fail == 0 else "❌ 存在不一致\n"))
    return fail


def cross_check_grid():
    print("## 4. 对拍 B：手写规则 vs CLDR 规则文本解析器（密集网格）\n")
    print("同一批输入分别用手写规则（plural.py）和独立解析 CLDR 规则文本"
          "（cldr_parser.py 直接解释 cldr_data.py 中的官方规则串）判定，"
          "逐条比对。\n")
    values = [str(k) for k in range(0, 2001)]
    values += [str(-k) for k in range(0, 201)]
    values += [f"{k}.{d}" for k in range(0, 121) for d in
               ("0", "00", "1", "5", "25", "99")]
    values += [str(10 ** e) for e in range(1, 31)]
    values += [str(10 ** e + 1) for e in range(1, 31)]
    values += [str(10 ** 30 + 10 ** 15 + 7)]
    total = mismatch = 0
    print("| 语言 | 类型 | 比对数 | 不一致 |")
    print("|---|---|---|---|")
    for loc in LOCALES:
        for kind, rules in (("cardinal", CARDINAL_RULES),
                            ("ordinal", ORDINAL_RULES)):
            loc_total = loc_mismatch = 0
            for value in values:
                op = plural.operands_of(value)
                want = evaluate(rules[loc], op)
                got = plural.plural_category(loc, value,
                                             ordinal=(kind == "ordinal"))
                loc_total += 1
                if want != got:
                    loc_mismatch += 1
                    if loc_mismatch <= 5:
                        print(f"  - 不一致: {loc}/{kind} `{value}` "
                              f"解析器={want} 手写={got}")
            total += loc_total
            mismatch += loc_mismatch
            print(f"| {loc} | {kind} | {loc_total} | {loc_mismatch} |")
    print(f"\n合计比对 {total} 次，不一致 {mismatch} 次。"
          + ("✅ 两套实现完全一致\n" if mismatch == 0 else "❌ 存在不一致\n"))
    return mismatch


def benchmark():
    print("## 5. 耗时：100 万个（数字 × 语言）组合\n")
    rng = random.Random(42)
    pool = (
        [str(rng.randrange(0, 10 ** 6)) for _ in range(400)]
        + [f"{rng.randrange(0, 10 ** 4)}.{rng.randrange(0, 100):02d}"
           for _ in range(300)]
        + [str(-rng.randrange(0, 10 ** 5)) for _ in range(150)]
        + ["0", "1", "2"] * 50
        + [str(rng.randrange(10 ** 20, 10 ** 30)) for _ in range(150)]
    )
    combos = [(LOCALES[i % len(LOCALES)], pool[rng.randrange(len(pool))])
              for i in range(1_000_000)]
    assert len(combos) == 1_000_000
    start = time.perf_counter()
    counts = {}
    for loc, value in combos:
        cat = plural.plural_category(loc, value)
        counts[cat] = counts.get(cat, 0) + 1
    elapsed = time.perf_counter() - start
    print(f"- 环境：Python {platform.python_version()} / "
          f"{platform.machine()} / {platform.system()}")
    print(f"- 组合构成：整数 40%、小数 30%、负数 15%、零/一/二 7.5%、"
          f"10^20~10^30 极大数 7.5%，5 种语言均匀分布")
    print(f"- 总耗时：**{elapsed:.3f} 秒**（含格式化 + 操作数计算 + 类别判定）")
    print(f"- 吞吐：{1_000_000 / elapsed:,.0f} 次/秒，"
          f"平均 {elapsed * 1e6 / 1_000_000:.2f} µs/次")
    print(f"- 类别分布：{dict(sorted(counts.items()))}\n")


def main():
    print(f"# 复数形式选择自测报告（{time.strftime('%Y-%m-%d %H:%M:%S')}）\n")
    edge_case_tables()
    formatting_linkage()
    failures = cross_check_samples()
    failures += cross_check_grid()
    benchmark()
    if failures:
        print(f"RESULT: FAIL ({failures} mismatches)")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
