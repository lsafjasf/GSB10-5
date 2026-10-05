from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wordfreq import PROFILES, count_tokens  # noqa: E402


PROFILE_ORDER = ("unicode-v1", "loose-v1")


def build_report(text: str) -> str:
    counts_by_profile = {
        profile_name: count_tokens(text, PROFILES[profile_name])
        for profile_name in PROFILE_ORDER
    }
    all_tokens = sorted(set().union(*(counts.keys() for counts in counts_by_profile.values())))

    lines = [
        "# 两种口径对照",
        "",
        "- `unicode-v1`：NFKC 规范化；`casefold()` 小写化；连字符拆分；撇号连接；CJK 表意文字逐字；其余标点仅作边界。",
        "- `loose-v1`：NFKC 规范化；保留原始大小写；连字符连接；撇号连接；CJK 表意文字逐字；其余标点仅作边界。",
        "",
        "## 汇总",
        "",
        "| 口径 | 总词次 | 唯一词数 |",
        "| --- | ---: | ---: |",
    ]

    for profile_name in PROFILE_ORDER:
        counts = counts_by_profile[profile_name]
        lines.append(f"| `{profile_name}` | {sum(counts.values())} | {len(counts)} |")

    lines.extend(
        [
            "",
            "## 对照数据",
            "",
            "| token | `unicode-v1` | `loose-v1` |",
            "| --- | ---: | ---: |",
        ]
    )
    for token in all_tokens:
        unicode_count = counts_by_profile["unicode-v1"].get(token, 0)
        loose_count = counts_by_profile["loose-v1"].get(token, 0)
        lines.append(f"| `{token}` | {unicode_count} | {loose_count} |")

    lines.extend(
        [
            "",
            "## 差异来源",
            "",
            "- 大小写：`unicode-v1` 把 `Hello` 与 `HELLO` 归并为 `hello`；`loose-v1` 保留为两个 token。",
            "- 连字符：`unicode-v1` 把 `well-known` 计为 `well` 和 `known`；`loose-v1` 计为一个 `well-known`。",
            "- 中文与标点：两种口径一致，CJK 表意文字逐字计数，标点只作为边界且不产生 token。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a two-profile comparison report")
    parser.add_argument("--input", type=Path, default=ROOT / "data" / "sample_text.txt")
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "profile_comparison.md")
    args = parser.parse_args()

    text = args.input.read_text(encoding="utf-8")
    report = build_report(text)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
