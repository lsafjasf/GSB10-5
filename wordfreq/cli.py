from __future__ import annotations

import argparse
import json
import sys

from .core import PROFILES, count_summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deterministic word-frequency counter")
    parser.add_argument(
        "--profile",
        choices=sorted(PROFILES),
        default="unicode-v1",
        help="tokenization profile (default: unicode-v1)",
    )
    parser.add_argument(
        "--input",
        "-i",
        help="UTF-8 input file; omit or use '-' to read standard input",
    )
    parser.add_argument(
        "--top",
        type=int,
        help="only print the first N tokens",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print a JSON report instead of token<TAB>count lines",
    )
    return parser


def _read_input(path: str | None) -> str:
    if path is None or path == "-":
        return sys.stdin.read()
    with open(path, "r", encoding="utf-8") as input_file:
        return input_file.read()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    text = _read_input(args.input)
    summary = count_summary(text, PROFILES[args.profile])

    if args.top is not None:
        if args.top < 0:
            raise SystemExit("--top must be non-negative")
        summary["counts"] = summary["counts"][: args.top]

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        for item in summary["counts"]:
            print(f"{item['token']}\t{item['count']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
