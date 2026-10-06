"""每条规则的单元级测试：行号、列、所在结构。"""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from revcheck import check_project, check_source  # noqa: E402

FIXTURES = os.path.join(REPO_ROOT, "fixtures")


def ids(findings):
    return sorted(f.rule_id for f in findings)


class RuleLineTests(unittest.TestCase):
    def test_rv001_points_at_default_expression(self):
        src = "def f(\n    a,\n    b=[]\n):\n    return b\n"
        out = check_source(src, path="x.py")
        hits = [f for f in out if f.rule_id == "RV001"]
        self.assertEqual(len(hits), 1)
        self.assertEqual((hits[0].line, hits[0].col), (3, 6))
        self.assertEqual(hits[0].structure, "function:f")

    def test_rv002_and_no_double_report_with_rv005(self):
        src = "try:\n    pass\nexcept:\n    pass\n"
        out = check_source(src, path="x.py")
        self.assertEqual(ids(out), ["RV002"])

    def test_rv003_fires_when_name_only_in_comment(self):
        src = "import sys\n# sys.exit(0)\nx = 1\n"
        out = check_source(src, path="x.py")
        self.assertEqual([f.rule_id for f in out], ["RV003"])
        self.assertEqual(out[0].line, 1)

    def test_rv004_points_at_compare_expression(self):
        src = "x = 1\nif x == None:\n    pass\n"
        out = check_source(src, path="x.py")
        self.assertEqual([f.rule_id for f in out], ["RV004"])
        self.assertEqual(out[0].line, 2)

    def test_rv004_ignores_int_zero_one(self):
        # 回归: 1 == True / 0 == False 曾让整数比较被误判
        src = "if len(a) == 1:\n    pass\nif i % 10 == 0:\n    pass\n"
        self.assertEqual(check_source(src, path="x.py"), [])

    def test_rv003_future_and_dunder_all_exempt(self):
        src = (
            "from __future__ import annotations\n"
            "from .core import Thing\n"
            '__all__ = ["Thing"]\n'
        )
        self.assertEqual(check_source(src, path="x.py"), [])

    def test_rv006_points_at_first_unreachable_statement(self):
        src = "def f():\n    return 1\n    a = 2\n    b = 3\n"
        out = check_source(src, path="x.py")
        hits = [f for f in out if f.rule_id == "RV006"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].line, 3)
        self.assertEqual(hits[0].structure, "function:f")

    def test_rv007_points_at_earlier_definition(self):
        src = "def f(): pass\ndef f(): pass\ndef f(): pass\n"
        out = check_source(src, path="x.py")
        hits = [f for f in out if f.rule_id == "RV007"]
        self.assertEqual([h.line for h in hits], [1])

    def test_rv008_ignores_attribute_form(self):
        src = "obj.eval('x')\neval('1')\n"
        out = check_source(src, path="x.py")
        hits = [f for f in out if f.rule_id == "RV008"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].line, 2)

    def test_fake_issues_in_string_and_comment_not_reported(self):
        src = (
            "# def f(x=[])\n"
            "TEXT = \"except: pass\\neval(1)\\nx == None\"\n"
            "x = 1\n"
        )
        self.assertEqual(check_source(src, path="x.py"), [])

    def test_multiline_string_with_fake_code_is_clean(self):
        src = 'S = """\ndef f(x=[]):\n    return eval(x)\n"""\n'
        self.assertEqual(check_source(src, path="x.py"), [])

    def test_nested_structure_name(self):
        src = (
            "class C:\n"
            "    def m(self):\n"
            "        def inner(v={}):\n"
            "            return v\n"
            "        return inner()\n"
        )
        out = check_source(src, path="x.py")
        hits = [f for f in out if f.rule_id == "RV001"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].line, 3)
        self.assertEqual(
            hits[0].structure, "nested:C.m.<locals>.inner"
        )


class CrossFileTests(unittest.TestCase):
    def test_rv009_exact_locations(self):
        out = check_project(os.path.join(FIXTURES, "crossfile"))
        hits = sorted(
            (f for f in out if f.rule_id == "RV009"),
            key=lambda f: (f.path, f.line),
        )
        self.assertEqual(len(hits), 2)
        self.assertEqual(
            [(os.path.basename(h.path), h.line, h.structure) for h in hits],
            [
                ("main.py", 4, "module"),
                ("main.py", 12, "function:bad_call"),
            ],
        )

    def test_valid_cross_file_usage_not_flagged(self):
        out = check_project(os.path.join(FIXTURES, "crossfile"))
        bad = [
            f
            for f in out
            if f.rule_id == "RV009"
            and f.line in (8, 16, 3, 20)
        ]
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
