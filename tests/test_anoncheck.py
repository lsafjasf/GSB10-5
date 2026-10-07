"""Self-tests for anoncheck (run: python3 -m unittest discover -s tests)."""

from __future__ import annotations

import csv
import os
import tempfile
import unittest

from anoncheck import (
    build_report,
    count_classes,
    find_risky_records,
    get_transform,
    parse_generalize_arg,
    risky_class_map,
    screen_combinations,
    suppress_records,
)
from anoncheck.hierarchies import age_band, zip_prefix


def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)


class AnonCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "data.csv")
        self.header = ["record_id", "age", "gender", "zipcode", "occupation"]

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, rows):
        write_csv(self.path, self.header, rows)

    def test_unique_combination_is_located_to_concrete_record(self):
        # Scenario A: four people share one tuple, one person is unique.
        rows = [
            ["r1", 30, "F", "100", "nurse"],
            ["r2", 30, "F", "100", "nurse"],
            ["r3", 30, "F", "100", "nurse"],
            ["r4", 30, "F", "100", "nurse"],
            ["r5", 97, "M", "999", "astronaut"],
        ]
        self._write(rows)
        qi = ["age", "gender", "zipcode", "occupation"]
        counts, total = count_classes(self.path, qi)
        report = build_report(counts, total, qi, k=2)
        self.assertEqual(total, 5)
        self.assertEqual(report.num_classes, 2)
        self.assertEqual(report.min_class_size, 1)
        self.assertEqual(report.max_class_size, 4)
        self.assertEqual(report.unique_classes, 1)
        self.assertEqual(report.unique_records, 1)
        self.assertEqual(report.max_reident_probability, 1.0)
        self.assertFalse(report.is_k_anonymous)

        risky = risky_class_map(counts, 2)
        located = find_risky_records(self.path, qi, risky)
        self.assertEqual(len(located), 1)
        self.assertEqual(located[0]["row"], 6)  # header is row 1
        self.assertEqual(located[0]["record_id"] if "record_id" in located[0]
                         else located[0]["row"], 6)
        self.assertEqual(located[0]["occupation"], "astronaut")
        self.assertEqual(located[0]["class_size"], 1)

    def test_large_equivalence_class_is_safe(self):
        # Scenario B: 200 identical records form one big class.
        rows = [["r%d" % i, 35, "F", "310025", "nurse"] for i in range(200)]
        self._write(rows)
        qi = ["age", "gender", "zipcode", "occupation"]
        counts, total = count_classes(self.path, qi)
        report = build_report(counts, total, qi, k=5)
        self.assertEqual(report.num_classes, 1)
        self.assertEqual(report.min_class_size, 200)
        self.assertEqual(report.max_class_size, 200)
        self.assertEqual(report.rare_classes, 0)
        self.assertEqual(report.rare_records, 0)
        self.assertTrue(report.is_k_anonymous)
        self.assertEqual(report.max_reident_probability, 1 / 200)

    def test_rare_values_create_rare_classes(self):
        # Scenario C: 'astronaut' occurs twice, producing a k<5 class.
        rows = [["r%d" % i, 40, "M", "100", "clerk"] for i in range(40)]
        rows += [["s1", 50, "F", "100", "astronaut"],
                 ["s2", 51, "F", "100", "astronaut"]]
        self._write(rows)
        qi = ["age", "gender", "zipcode", "occupation"]
        counts, total = count_classes(self.path, qi)
        report = build_report(counts, total, qi, k=5)
        self.assertEqual(report.rare_classes, 2)
        self.assertEqual(report.rare_records, 2)
        located = find_risky_records(self.path, qi,
                                     risky_class_map(counts, 5))
        self.assertEqual({r["occupation"] for r in located}, {"astronaut"})

    def test_single_fields_safe_but_combination_identifies(self):
        # Scenario D: each field alone splits 4 records 2/2; the pair is unique.
        rows = [
            ["r1", 30, "M", "100", "teacher"],
            ["r2", 30, "F", "200", "teacher"],
            ["r3", 40, "M", "200", "teacher"],
            ["r4", 40, "F", "100", "teacher"],
        ]
        self._write(rows)
        for single in ("age", "gender", "zipcode"):
            counts, total = count_classes(self.path, [single])
            self.assertEqual(build_report(counts, total, [single], k=2).min_class_size, 2)
        qi = ["age", "gender", "zipcode"]
        counts, total = count_classes(self.path, qi)
        report = build_report(counts, total, qi, k=2)
        self.assertEqual(report.num_classes, 4)
        self.assertEqual(report.unique_classes, 4)
        self.assertEqual(report.max_reident_probability, 1.0)

    def test_screening_covers_singles_pairs_and_full_set(self):
        rows = [
            ["r1", 30, "M", "100", "teacher"],
            ["r2", 30, "F", "200", "teacher"],
            ["r3", 40, "M", "200", "teacher"],
            ["r4", 40, "F", "100", "teacher"],
        ]
        self._write(rows)
        results = screen_combinations(self.path, ["age", "gender", "zipcode"], k=2)
        combos = {tuple(r["combination"]) for r in results}
        self.assertEqual(len(results), 7)  # 3 singles + 3 pairs + 1 full
        full = next(r for r in results if len(r["combination"]) == 3)
        self.assertEqual(full["unique_classes"], 4)

    def test_generalization_band_and_prefix(self):
        self.assertEqual(age_band(10)("37"), "30-39")
        self.assertEqual(age_band(5)("37"), "35-39")
        self.assertEqual(zip_prefix(3)("310025"), "310***")
        self.assertEqual(zip_prefix(0)("AB"), "**")

    def test_generalization_reduces_risk_before_after(self):
        rows = [
            ["r1", 31, "M", "100", "clerk"], ["r2", 32, "M", "100", "clerk"],
            ["r3", 33, "M", "100", "clerk"], ["r4", 34, "M", "100", "clerk"],
            ["r5", 35, "M", "100", "clerk"],
        ]
        self._write(rows)
        qi = ["age", "gender", "zipcode", "occupation"]
        raw_counts, total = count_classes(self.path, qi)
        before = build_report(raw_counts, total, qi, k=5)
        self.assertEqual(before.unique_classes, 5)  # every exact age unique

        specs = parse_generalize_arg("age=age_band:10,zipcode=identity")
        transforms = [get_transform(specs.get(f, "identity")) for f in qi]
        gen_counts, _ = count_classes(self.path, qi, transforms)
        after = build_report(gen_counts, total, qi, k=5,
                             transform_names=[specs.get(f, "identity")
                                              for f in qi])
        self.assertEqual(after.num_classes, 1)
        self.assertEqual(after.min_class_size, 5)
        self.assertTrue(after.is_k_anonymous)
        self.assertEqual(after.rare_records, 0)

    def test_suppression_drop_and_mask_achieve_k(self):
        rows = [
            ["r1", 30, "F", "100", "nurse"], ["r2", 30, "F", "100", "nurse"],
            ["r3", 30, "F", "100", "nurse"], ["r4", 30, "F", "100", "nurse"],
            ["r5", 97, "M", "999", "astronaut"],
        ]
        self._write(rows)
        qi = ["age", "gender", "zipcode", "occupation"]
        counts, _ = count_classes(self.path, qi)
        risky = risky_class_map(counts, 2)

        dropped = os.path.join(self.tmp.name, "dropped.csv")
        stats = suppress_records(self.path, dropped, qi, risky, mode="drop")
        self.assertEqual(stats["dropped"], 1)
        d_counts, d_total = count_classes(dropped, qi)
        self.assertTrue(build_report(d_counts, d_total, qi, k=2).is_k_anonymous)

        masked = os.path.join(self.tmp.name, "masked.csv")
        stats = suppress_records(self.path, masked, qi, risky, mode="mask")
        self.assertEqual(stats["masked"], 1)
        with open(masked, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("*,*,*,*", body)

    def test_empty_dataset_report(self):
        write_csv(self.path, self.header, [])
        qi = ["age", "gender"]
        counts, total = count_classes(self.path, qi)
        report = build_report(counts, total, qi, k=5)
        self.assertEqual(report.num_classes, 0)
        self.assertFalse(report.is_k_anonymous)


if __name__ == "__main__":
    unittest.main()
