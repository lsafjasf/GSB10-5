"""生成确定性的对拍数据集 golden_cases.jsonl（固定种子）。"""

import json
import os
import random

from .registry import FORMATS, ALIASES, FIELD_TYPES
from .tests_oracle import oracle_negotiate

FORMAT_TOKENS = list(FORMATS) + list(ALIASES) + ["docx", "parquet", "avro", "xls"]


def main():
    rng = random.Random(20261006)
    cases = []
    for _ in range(2000):
        fmt = rng.choice(FORMAT_TOKENS)
        n = rng.randint(0, 6)
        fields = []
        for i in range(n):
            fields.append({
                "name": "f%d_%s" % (i, rng.choice(["id", "name", "ts", "blob", "doc", "flag", "amt"])),
                "type": rng.choice(FIELD_TYPES),
            })
        status, selected, missing = oracle_negotiate(fmt, fields)
        cases.append({
            "requested_format": fmt,
            "fields": fields,
            "expect_status": status,
            "expect_selected": selected,
            "expect_missing": missing,
        })
    out = os.path.join(os.path.dirname(__file__), "golden_cases.jsonl")
    with open(out, "w", encoding="utf-8") as fh:
        for case in cases:
            fh.write(json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n")
    print("wrote %d cases -> %s" % (len(cases), out))


if __name__ == "__main__":
    main()
