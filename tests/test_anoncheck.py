"""anoncheck 自测：覆盖唯一组合、大等价类、稀有取值、字段组合敏感、
泛化与抑制等情形。运行：python -m unittest discover -s tests -v
"""

import unittest

from anoncheck.quasi import identify_quasi_identifiers, enumerate_combinations
from anoncheck.risk import evaluate_combination, find_risky_records
from anoncheck.transform import (
    age_band, zip_prefix, category_mapper,
    apply_generalization, suppress_risky_records,
)
from anoncheck.dataset import generate, HEADERS, NEEDLES, OCCUPATION_CATEGORY


class TestQuasiIdentifierIdentification(unittest.TestCase):
    def test_direct_identifier_and_sensitive_excluded(self):
        headers = ["record_id", "name", "age", "gender", "disease"]
        rows = [
            ("N1", "alice", 30, "F", "flu"),
            ("N2", "bob", 31, "M", "flu"),
            ("N3", "carol", 30, "F", "asthma"),
        ]
        qis = identify_quasi_identifiers(headers, rows)
        columns = [q["column"] for q in qis]
        self.assertIn("age", columns)
        self.assertIn("gender", columns)
        self.assertNotIn("record_id", columns)  # 直接标识符
        self.assertNotIn("name", columns)       # 直接标识符
        self.assertNotIn("disease", columns)    # 敏感属性

    def test_statistical_fallback_for_unknown_column(self):
        headers = ["col_x", "col_y"]
        rows = [(f"v{i % 7}", f"u{i}") for i in range(100)]
        qis = identify_quasi_identifiers(headers, rows)
        columns = [q["column"] for q in qis]
        # col_x 基数 7/100，处于可组合区间 -> 组合型 QI
        self.assertIn("col_x", columns)
        # col_y 每行唯一 -> 近唯一，按直接标识符排除
        self.assertNotIn("col_y", columns)

    def test_constant_column_excluded(self):
        headers = ["age", "country"]
        rows = [(30, "CN"), (31, "CN"), (32, "CN")]
        qis = identify_quasi_identifiers(headers, rows)
        columns = [q["column"] for q in qis]
        self.assertIn("age", columns)
        self.assertNotIn("country", columns)

    def test_combination_enumeration_order(self):
        headers = ["age", "gender", "zip_code"]
        rows = [(30, "F", "100000"), (31, "M", "100001")]
        qis = identify_quasi_identifiers(headers, rows)
        combos = enumerate_combinations(qis, max_order=2)
        names = [c[0] for c in combos]
        self.assertEqual(
            names,
            [("age",), ("gender",), ("zip_code",),
             ("age", "gender"), ("age", "zip_code"),
             ("gender", "zip_code")])


class TestEquivalenceClassRisk(unittest.TestCase):
    def test_unique_combination_detected(self):
        """唯一组合：一条记录的组合取值在数据集中只出现一次。"""
        headers = ["age", "gender"]
        rows = [(30, "F")] * 6 + [(99, "M")]
        idx = (0, 1)
        metrics = evaluate_combination(rows, idx, k_threshold=5)
        self.assertEqual(metrics["min_class_size"], 1)
        self.assertEqual(metrics["unique_class_count"], 1)
        self.assertEqual(metrics["unique_record_count"], 1)
        self.assertEqual(metrics["prosecutor_risk"], 1.0)
        risky = find_risky_records(rows, idx, k_threshold=5)
        self.assertEqual(len(risky), 1)
        self.assertEqual(risky[0]["row_number"], 6)
        self.assertEqual(risky[0]["key"], (99, "M"))

    def test_large_equivalence_class(self):
        """大等价类：全部记录取值相同，k-匿名值等于记录数。"""
        rows = [("F", "100000")] * 1000
        metrics = evaluate_combination(rows, (0, 1), k_threshold=5)
        self.assertEqual(metrics["class_count"], 1)
        self.assertEqual(metrics["min_class_size"], 1000)
        self.assertEqual(metrics["max_class_size"], 1000)
        self.assertEqual(metrics["rare_class_count"], 0)
        self.assertEqual(metrics["rare_record_count"], 0)
        self.assertEqual(find_risky_records(rows, (0, 1), 5), [])

    def test_rare_values_form_rare_combinations(self):
        """稀有取值：低频邮编使含它的组合成为稀有组合。"""
        rows = [("F", "100000")] * 50 + [("F", "99999")] * 2
        metrics = evaluate_combination(rows, (0, 1), k_threshold=5)
        self.assertEqual(metrics["class_count"], 2)
        self.assertEqual(metrics["rare_class_count"], 1)
        self.assertEqual(metrics["rare_record_count"], 2)
        risky = find_risky_records(rows, (0, 1), k_threshold=5)
        self.assertEqual(len(risky), 2)
        self.assertTrue(all(r["key"] == ("F", "99999") for r in risky))

    def test_sensitive_field_combination(self):
        """字段组合敏感：单列均无唯一值，三列组合却唯一。"""
        # 立方体的 8 个三元组各出现两次：单列、两列、三列都无唯一类；
        # 再删掉其中一个三元组的一个副本 -> 仅三列组合出现一个唯一类。
        triples = [(a, b, c) for a in (0, 1)
                   for b in (0, 1) for c in (0, 1)]
        rows = [t for t in triples for _ in range(2)]
        rows.remove((1, 0, 1))
        # 每个单列都不存在唯一取值
        for i in range(3):
            m = evaluate_combination(rows, (i,), k_threshold=2)
            self.assertEqual(m["unique_class_count"], 0)
        # 两列组合也都不唯一
        for pair in [(0, 1), (0, 2), (1, 2)]:
            m = evaluate_combination(rows, pair, k_threshold=2)
            self.assertEqual(m["unique_class_count"], 0)
        # 三列组合出现唯一等价类，可定位到具体记录
        m = evaluate_combination(rows, (0, 1, 2), k_threshold=2)
        self.assertEqual(m["unique_class_count"], 1)
        risky = find_risky_records(rows, (0, 1, 2), k_threshold=2)
        self.assertEqual(len(risky), 1)
        self.assertEqual(risky[0]["key"], (1, 0, 1))


class TestMitigation(unittest.TestCase):
    def setUp(self):
        self.headers = ["age", "gender", "zip_code", "occupation"]
        self.rows = [
            (34, "F", "100084", "nurse"),
            (35, "F", "100085", "nurse"),
            (36, "F", "100086", "nurse"),
            (79, "M", "999999", "astronaut"),  # 唯一组合
        ]
        self.idx = (0, 1, 2, 3)

    def test_generalization_reduces_risk(self):
        before = evaluate_combination(self.rows, self.idx, k_threshold=5)
        self.assertEqual(before["min_class_size"], 1)
        plan = {
            0: age_band(10),
            2: zip_prefix(3),
            3: category_mapper(OCCUPATION_CATEGORY),
        }
        gen_rows = apply_generalization(self.rows, plan)
        # 前三条护士记录泛化后落入同一等价类
        self.assertEqual(gen_rows[0], gen_rows[1])
        self.assertEqual(gen_rows[1], gen_rows[2])
        after = evaluate_combination(gen_rows, self.idx, k_threshold=5)
        self.assertLess(after["class_count"], before["class_count"])
        self.assertLess(after["unique_class_count"],
                        before["unique_class_count"])
        # 针尖记录仍唯一，说明单靠泛化不够，需继续抑制（见下一测试）
        self.assertEqual(after["unique_class_count"], 1)

    def test_suppression_masks_risky_records(self):
        before = evaluate_combination(self.rows, self.idx, k_threshold=5)
        self.assertEqual(before["unique_record_count"], 4)
        sup_rows, suppressed = suppress_risky_records(
            self.rows, self.idx, k_threshold=5)
        self.assertEqual(suppressed, 4)
        after = evaluate_combination(sup_rows, self.idx, k_threshold=5)
        # 被抑制记录组合列全为掩码，彼此不可区分
        self.assertEqual(after["unique_class_count"], 0)
        self.assertEqual(after["min_class_size"], 4)

    def test_before_after_comparison_consistent(self):
        plan = {0: age_band(10), 2: zip_prefix(3)}
        gen_rows = apply_generalization(self.rows, plan)
        before = evaluate_combination(self.rows, self.idx, k_threshold=5)
        after = evaluate_combination(gen_rows, self.idx, k_threshold=5)
        self.assertGreaterEqual(after["mean_class_size"],
                                before["mean_class_size"])
        self.assertLessEqual(after["rare_record_count"],
                             before["rare_record_count"])


class TestSyntheticDataset(unittest.TestCase):
    def test_planted_needles_are_unique(self):
        """植入的针尖记录在四列组合上必然唯一，且能被定位。"""
        headers, rows = generate(2000, seed=1)
        self.assertEqual(headers, HEADERS)
        idx = (2, 3, 4, 5)  # age, gender, zip_code, occupation
        risky = find_risky_records(rows, idx, k_threshold=2, id_index=0)
        needle_keys = {(n[0], n[1], n[2], n[3]) for n in NEEDLES}
        found_keys = {r["key"] for r in risky}
        self.assertTrue(needle_keys <= found_keys)
        # 清单中的记录 ID 与行号对应原数据，可复核
        for rec in risky:
            self.assertEqual(rows[rec["row_number"]][0], rec["record_id"])

    def test_large_class_exists(self):
        """性别单列应形成两个大等价类。"""
        _, rows = generate(5000, seed=2)
        m = evaluate_combination(rows, (3,), k_threshold=5)
        self.assertEqual(m["class_count"], 2)
        self.assertGreater(m["min_class_size"], 1000)


if __name__ == "__main__":
    unittest.main()
