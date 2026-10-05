#!/usr/bin/env python3
"""Self tests for plural.py.

Group 1 (differential): every sample shipped in the CLDR chart v47 for
en/ru/ar/fr (data/cldr_rules_samples.json, 427 values) must map to the
category stated by CLDR.

Group 2 (boundaries): zero / one / decimals / negatives / huge numbers.

Group 3 (formatting linkage): fraction digits, grouping, ordinals.
"""
import json
import unittest
from decimal import Decimal

from plural import operands, plural_category, select_plural, format_number

CLDR = json.load(open("data/cldr_rules_samples.json", encoding="utf-8"))


class TestCldrSamples(unittest.TestCase):
    """Differential test against the CLDR rule data source, sample by sample."""

    def test_all_cldr_samples(self):
        checked = 0
        failures = []
        for lang, types in CLDR["languages"].items():
            for typ, cats in types.items():
                ordinal = (typ == "ordinal")
                for cat, info in cats.items():
                    for sample in info["samples"]:
                        got = plural_category(lang, sample, ordinal=ordinal)
                        if got != cat:
                            failures.append(
                                "%s/%s: sample %r -> %s, expected %s"
                                % (lang, typ, sample, got, cat))
                        checked += 1
        self.assertEqual(failures, [])
        print("differential: %d CLDR samples checked, 0 mismatches" % checked)


class TestBoundaries(unittest.TestCase):
    """Boundary values per language (cardinal unless noted)."""

    CASES = [
        # (value, en, ru, ar, fr)
        ("0",        "other", "many", "zero", "one"),
        ("0.0",      "other", "other", "zero", "one"),
        ("1",        "one",   "one",  "one",  "one"),
        ("1.0",      "other", "other", "one",  "one"),   # v=1 kills en/ru "one"
        ("2",        "other", "few",  "two",  "other"),
        ("3",        "other", "few",  "few",  "other"),
        ("0.5",      "other", "other", "other", "one"),  # fr: i=0 -> one
        ("1.5",      "other", "other", "other", "one"),  # fr: i=1 -> one
        ("2.5",      "other", "other", "other", "other"),
        ("5",        "other", "many", "few",  "other"),
        ("11",       "other", "many", "many", "other"),
        ("12",       "other", "many", "many", "other"),
        ("14",       "other", "many", "many", "other"),
        ("21",       "other", "one",  "many", "other"),
        ("22",       "other", "few",  "many", "other"),
        ("25",       "other", "many", "many", "other"),
        ("99",       "other", "many", "many", "other"),
        ("101",      "other", "one",  "other", "other"),
        ("102",      "other", "few",  "other", "other"),
        ("111",      "other", "many", "many", "other"),
        ("1000",     "other", "many", "other", "other"),
        ("1000000",  "other", "many", "other", "many"),  # fr millions -> many
        ("2000000",  "other", "many", "other", "many"),
        # negatives use the absolute value (CLDR operand n)
        ("-1",       "one",   "one",  "one",  "one"),
        ("-2",       "other", "few",  "two",  "other"),
        ("-0.5",     "other", "other", "other", "one"),
        ("-1.5",     "other", "other", "other", "one"),
        ("-1000000", "other", "many", "other", "many"),
        # huge numbers
        (str(10**10),       "other", "many", "other", "many"),  # 1e10 % 1e6 == 0
        (str(10**30),       "other", "many", "other", "many"),  # 10**30 % 10**6 == 0
        (str(10**30 + 1),   "other", "one",  "other", "other"),
        ("123456789012345678901234567890.5", "other", "other", "other", "other"),
    ]

    def test_boundaries(self):
        langs = ["en", "ru", "ar", "fr"]
        for row in self.CASES:
            value, expected = row[0], row[1:]
            for lang, want in zip(langs, expected):
                got = plural_category(lang, value)
                self.assertEqual(got, want,
                                 "%s cardinal %r -> %s, want %s"
                                 % (lang, value, got, want))

    def test_extreme_magnitudes(self):
        big = 10**40
        self.assertEqual(plural_category("en", big), "other")
        self.assertEqual(plural_category("ru", big + 2), "few")
        self.assertEqual(plural_category("ar", big + 1), "other")  # n%100=1, no range
        self.assertEqual(plural_category("fr", big), "many")  # e=0, i%1e6==0
        tiny = Decimal("0.0000001")
        self.assertEqual(plural_category("en", tiny), "other")
        self.assertEqual(plural_category("ar", tiny), "other")
        self.assertEqual(plural_category("fr", tiny), "one")  # i=0


class TestFormattingLinkage(unittest.TestCase):
    """Category must follow the *formatted* number, not the raw value."""

    def test_fraction_digits_change_category(self):
        # en: integer 1 is "one"; rendered with one decimal it becomes "other"
        self.assertEqual(select_plural("en", 1).category, "one")
        r = select_plural("en", 1, min_frac=1)
        self.assertEqual((r.formatted, r.category), ("1.0", "other"))
        # ru: 1.5 rounded to 0 decimals becomes "2" -> few; kept -> other
        self.assertEqual(select_plural("ru", 1.5, max_frac=0).category, "few")
        self.assertEqual(select_plural("ru", 1.5, max_frac=2).category, "other")
        # ar: 1.0 with decimals is still n=1 -> one
        self.assertEqual(select_plural("ar", 1, min_frac=2).category, "one")

    def test_grouping_does_not_change_category(self):
        grouped = select_plural("ru", 1234561, grouping=True)
        plain = select_plural("ru", 1234561, grouping=False)
        self.assertEqual(grouped.formatted, "1,234,561")
        self.assertEqual(grouped.category, plain.category == "one" and "one")

    def test_rounding_half_even(self):
        self.assertEqual(format_number(2.5, max_frac=0), "2")
        self.assertEqual(format_number(3.5, max_frac=0), "4")
        self.assertEqual(format_number(-1234567.891, max_frac=2), "-1,234,567.89")

    def test_ordinals(self):
        en = {1: "one", 2: "two", 3: "few", 4: "other", 11: "other",
              12: "other", 13: "other", 21: "one", 22: "two", 23: "few",
              101: "one", 111: "other"}
        for n, want in en.items():
            self.assertEqual(plural_category("en", n, ordinal=True), want,
                             "en ordinal %d" % n)
        self.assertEqual(plural_category("fr", 1, ordinal=True), "one")
        self.assertEqual(plural_category("fr", 2, ordinal=True), "other")
        self.assertEqual(plural_category("ru", 1, ordinal=True), "other")
        self.assertEqual(plural_category("ar", 1, ordinal=True), "other")

    def test_operands(self):
        self.assertEqual(operands("1.00")[:4], (Decimal("1.00"), 1, 2, 0))
        self.assertEqual(operands("1.20").w, 1)
        self.assertEqual(operands("1.20").t, 2)
        self.assertEqual(operands("1.0000001c6").e, 6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
