import json
import unittest
from pathlib import Path

from phcheck import RenderError, restore

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "restore_cases.json"


def load_cases():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


class TestRestoreFixtures(unittest.TestCase):
    def test_all_cases(self):
        cases = load_cases()
        self.assertGreaterEqual(len(cases), 6)
        for case in cases:
            with self.subTest(id=case["id"]):
                out = restore(case["source"], case["translation"], case["values"])
                self.assertEqual(out, case["expect"])

    def test_native_cross_check(self):
        # 命名 / 裸占位符的还原结果必须与 Python 原生 % 格式化完全一致
        for case in load_cases():
            if case["id"].startswith(("positional", "hex_oct")):
                continue  # 原生 % 不支持 %n$ 位置参数
            with self.subTest(id=case["id"]):
                values = case["values"]
                native = case["translation"] % (values if isinstance(values, dict) else tuple(values))
                self.assertEqual(
                    restore(case["source"], case["translation"], values), native
                )


class TestRestoreMapping(unittest.TestCase):
    def test_positional_with_mapping_values(self):
        self.assertEqual(restore("%1$s/%2$s", "%2$s %1$s", {"1": "a", "2": "b"}), "b a")
        self.assertEqual(restore("%1$s/%2$s", "%2$s %1$s", {1: "a", 2: "b"}), "b a")

    def test_bare_reorder_same_type(self):
        self.assertEqual(restore("%s 和 %s", "%s & %s", ["x", "y"]), "x & y")

    def test_check_blocks_inconsistent_translation(self):
        with self.assertRaises(RenderError):
            restore("%(a)s", "%(a)s %(b)s", {"a": 1, "b": 2})

    def test_missing_value(self):
        with self.assertRaises(RenderError):
            restore("%(a)s", "%(a)s", {}, check=True)
        with self.assertRaises(RenderError):
            restore("%s %s", "%s %s", ["only-one"])

    def test_invalid_source_or_translation(self):
        with self.assertRaises(RenderError):
            restore("%*d", "%d", [1], check=False)
        with self.assertRaises(RenderError):
            restore("%d", "%z", [1], check=False)

    def test_type_mismatch_raises(self):
        with self.assertRaises(RenderError):
            restore("%d", "%d", ["not-a-number"])


if __name__ == "__main__":
    unittest.main()
