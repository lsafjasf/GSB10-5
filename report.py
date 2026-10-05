#!/usr/bin/env python3
"""Print the comparison tables (markdown) used in README.md:
boundary values, formatting linkage, and CLDR differential summary.
"""
import json
from plural import plural_category, select_plural

CLDR = json.load(open("data/cldr_rules_samples.json", encoding="utf-8"))
LANGS = ["en", "ru", "ar", "fr"]

BOUNDARY = ["0", "0.0", "1", "1.0", "2", "3", "0.5", "1.5", "2.5", "5",
            "11", "12", "14", "21", "22", "25", "99", "101", "102", "111",
            "1000", "1000000", "2000000",
            "-1", "-2", "-0.5", "-1.5", "-1000000",
            str(10**10), str(10**30), str(10**30 + 1),
            "123456789012345678901234567890.5"]

LINKAGE = [
    # (lang, value, kwargs, note)
    ("en", 1,     {},                    "integer 1"),
    ("en", 1,     {"min_frac": 1},       "same value, 1 fraction digit"),
    ("en", 1,     {"min_frac": 2},       "same value, 2 fraction digits"),
    ("ru", 1,     {},                    "integer 1"),
    ("ru", 1,     {"min_frac": 1},       "same value, 1 fraction digit"),
    ("ru", 1.5,   {"max_frac": 0},       "rounds to 2 at format time"),
    ("ru", 1.5,   {"max_frac": 2},       "keeps the fraction"),
    ("ru", 1234561, {"grouping": True},  "thousands grouping on"),
    ("ru", 1234561, {"grouping": False}, "thousands grouping off"),
    ("ar", 1,     {"min_frac": 2},       "n=1 regardless of v"),
    ("fr", 0.5,   {},                    "i=0 -> one"),
    ("fr", 1000000, {},                  "million -> many"),
    ("en", 1,     {"ordinal": True},     "ordinal: 1st"),
    ("en", 2,     {"ordinal": True},     "ordinal: 2nd"),
    ("en", 3,     {"ordinal": True},     "ordinal: 3rd"),
    ("en", 11,    {"ordinal": True},     "ordinal: 11th (exception)"),
    ("en", 21,    {"ordinal": True},     "ordinal: 21st"),
    ("fr", 1,     {"ordinal": True},     "ordinal: 1er"),
    ("fr", 2,     {"ordinal": True},     "ordinal: 2e"),
]


def main():
    print("### Boundary values (cardinal)\n")
    print("| value | " + " | ".join(LANGS) + " |")
    print("|" + "---|" * (len(LANGS) + 1))
    for v in BOUNDARY:
        row = [plural_category(lang, v) for lang in LANGS]
        print("| `%s` | %s |" % (v, " | ".join(row)))

    print("\n### Formatting linkage (select_plural)\n")
    print("| lang | input | options | formatted | category | note |")
    print("|---|---|---|---|---|---|")
    for lang, v, kw, note in LINKAGE:
        r = select_plural(lang, v, **kw)
        opts = ", ".join("%s=%r" % kv for kv in kw.items()) or "(default)"
        print("| %s | `%r` | %s | `%s` | **%s** | %s |"
              % (lang, v, opts, r.formatted, r.category, note))

    print("\n### CLDR differential summary\n")
    print("| lang | type | category | rule (CLDR v47) | samples checked |")
    print("|---|---|---|---|---|")
    total = 0
    for lang in LANGS:
        for typ, cats in CLDR["languages"][lang].items():
            for cat, info in cats.items():
                n = len(info["samples"])
                total += n
                rule = info["rule"] or "(default)"
                print("| %s | %s | %s | `%s` | %d |"
                      % (lang, typ, cat, rule, n))
    print("| | | **total** | | **%d** |" % total)


if __name__ == "__main__":
    main()
