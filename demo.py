"""演示四种典型情形的协商结果，输出 docs/examples.json。"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from export_negotiation import negotiate

DEMOS = {
    "exact_hit": ("json", [("id", "int"), ("name", "str"), ("created", "date")]),
    "alias_exact": ("jsonl", [("id", "int"), ("name", "str")]),
    "downgrade": ("xlsx", [("id", "int"), ("name", "str"), ("created", "date")]),
    "partial_fields": (
        "xlsx",
        [("id", "int"), ("tags", "array"), ("meta", "object")],
    ),
    "unsupported": ("parquet", [("blob", "binary")]),
    "unknown_format_best_effort": (
        "yaml",
        [("id", "int"), ("tags", "array")],
    ),
}


def main():
    out = {name: negotiate(fmt, fields) for name, (fmt, fields) in DEMOS.items()}
    text = json.dumps(out, indent=2, ensure_ascii=False, sort_keys=True)
    print(text)
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "docs", "examples.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")


if __name__ == "__main__":
    main()
