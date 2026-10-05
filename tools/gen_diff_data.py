"""Generate differential-test ("对拍") data: each filter expression is
evaluated both by safefilter and by the equivalent Python semantics, then
written to data/diff_cases.tsv with a verdict column.

Usage: python3 tools/gen_diff_data.py [count] [seed]
"""

import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from safefilter import evaluate
from tests.diffgen import generate_case

GOLDEN = [
    ("age >= 18", True),
    ("age >= 18 and vip == true", False),
    ("age >= 18 or vip == true", True),
    ("not (age < 18)", True),
    ("active or vip and age < 0", True),
    ("active and vip or age < 0", False),
    ("age >= 18 or vip == true and score > 0.9", True),
    ("age < 18 or age > 60 and vip == true", False),
    ("null == null", True),
    ("null == 1", False),
]


def main(count=500, seed=20261005):
    rng = random.Random(seed)
    out_path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "data", "diff_cases.tsv")
    mismatches = 0
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("id\texpression\trow_json\tpython_oracle\texpected\tactual\tverdict\n")

        def write(cid, source, row, py_source, expected):
            nonlocal mismatches
            actual = evaluate(source, row)
            verdict = "match" if bool(expected) == actual else "MISMATCH"
            if verdict != "match":
                mismatches += 1
            f.write("%d\t%s\t%s\t%s\t%s\t%s\t%s\n" % (
                cid, source, json.dumps(row, ensure_ascii=False),
                py_source, bool(expected), actual, verdict))

        golden_row = {
            "age": 20, "score": 0.95, "level": 3, "vip": False,
            "active": True, "city": "beijing", "name": "alice",
            "nick": "alpha", "grade": 88,
        }
        cid = 1
        for source, expected in GOLDEN:
            write(cid, source, golden_row, "", expected)
            cid += 1
        for _ in range(count):
            source, row, py_source = generate_case(rng)
            expected = eval(py_source, {"__builtins__": {}}, dict(row))
            write(cid, source, row, py_source, expected)
            cid += 1
    total = cid - 1
    print("wrote %d cases (%d golden + %d random) to %s"
          % (total, len(GOLDEN), count, out_path))
    print("mismatches: %d" % mismatches)
    return 1 if mismatches else 0


if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 20261005
    raise SystemExit(main(count, seed))
