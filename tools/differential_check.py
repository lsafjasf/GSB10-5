#!/usr/bin/env python3
"""Differential test harness: compare plural.py against every sample in the
CLDR v47 chart snapshot, row by row, and write data/differential_results.csv
as auditable evidence. Exits non-zero on any mismatch.
"""
import csv
import json
import sys

sys.path.insert(0, ".")
from plural import plural_category

CLDR = json.load(open("data/cldr_rules_samples.json", encoding="utf-8"))
OUT = "data/differential_results.csv"


def main():
    rows, mismatch = [], 0
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["lang", "type", "category_expected", "sample",
                    "category_actual", "match"])
        for lang, types in CLDR["languages"].items():
            for typ, cats in types.items():
                for cat, info in cats.items():
                    for sample in info["samples"]:
                        got = plural_category(lang, sample,
                                              ordinal=(typ == "ordinal"))
                        ok = got == cat
                        mismatch += not ok
                        w.writerow([lang, typ, cat, sample, got, int(ok)])
                        rows.append((lang, typ, cat, sample, got))
    print("wrote %s: %d rows, %d mismatches" % (OUT, len(rows), mismatch))
    # small stdout excerpt for each language
    for lang in CLDR["languages"]:
        sel = [r for r in rows if r[0] == lang and r[1] == "cardinal"]
        shown = ["%s:%s->%s" % (r[3], r[2], r[4])
                 for r in sel if r[3] in ("0", "1", "2", "5", "11",
                                          "21", "101", "1000000")]
        print("  %s: %s" % (lang, ", ".join(shown)))
    return 1 if mismatch else 0


if __name__ == "__main__":
    sys.exit(main())
