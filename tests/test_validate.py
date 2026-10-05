import json
import unittest
from pathlib import Path

from phcheck import validate

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "validation_cases.json"


def norm_diffs(diffs):
    return {d.ident: [d.source, d.translation] for d in diffs}


class TestValidateFixtures(unittest.TestCase):
    def test_all_cases(self):
        cases = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
        self.assertGreaterEqual(len(cases), 15)
        for case in cases:
            with self.subTest(id=case["id"]):
                report = validate(
                    case["source"],
                    case["translation"],
                    strict_format=case.get("strict_format", False),
                )
                expect = case["expect"]
                self.assertEqual(report.ok, expect["ok"], "ok 判定不一致")
                self.assertEqual(norm_diffs(report.missing), expect["missing"], "missing")
                self.assertEqual(norm_diffs(report.extra), expect["extra"], "extra")
                self.assertEqual(
                    norm_diffs(report.count_mismatch), expect["count_mismatch"], "count_mismatch"
                )
                self.assertEqual(len(report.invalid), len(expect["invalid"]), "invalid 条数")
                for item, exp in zip(report.invalid, expect["invalid"]):
                    self.assertEqual(item["where"], exp["where"])
                    self.assertIn(exp["reason_contains"], item["reason"])
                self.assertEqual(
                    [d.ident for d in report.format_drift], expect["format_drift"], "format_drift"
                )


class TestValidateDirect(unittest.TestCase):
    def test_categories_property(self):
        report = validate("%(a)s %(b)s", "%(b)s %(c)s")
        self.assertEqual(report.categories, ["missing", "extra"])
        self.assertFalse(report.ok)

    def test_duplicate_count_rule(self):
        # 重复占位符按身份计数：原文 3 次、译文 1 次 -> count_mismatch(3 -> 1)
        report = validate("%s %s %s", "%s")
        self.assertEqual(report.count_mismatch[0].source, 3)
        self.assertEqual(report.count_mismatch[0].translation, 1)
        self.assertEqual(report.count_mismatch[0].delta, -2)

    def test_as_dict_json_shape(self):
        report = validate("%(a)s", "%(a)s %(b)s")
        data = report.as_dict()
        self.assertEqual(set(data), {"ok", "missing", "extra", "count_mismatch", "invalid", "format_drift"})
        self.assertEqual(data["extra"][0]["ident"], "%(b)s")

    def test_strict_format_drift_not_in_default(self):
        self.assertEqual(validate("%(c)05d", "%(c)5d").format_drift, [])
        self.assertEqual(len(validate("%(c)05d", "%(c)5d", strict_format=True).format_drift), 1)


if __name__ == "__main__":
    unittest.main()
