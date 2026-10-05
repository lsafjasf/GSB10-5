"""Self tests for safefilter.

Run with:  python3 -m unittest discover -s tests -v
or simply: python3 run_tests.py
"""

import json
import os
import random
import unittest

from safefilter import (ExpressionTooDeep, ExpressionTooLong,
                        ForbiddenConstruct, MalformedLiteral,
                        SyntaxFilterError, TypeMismatch, UnknownField,
                        compile_expr, evaluate, parse)
from tests.diffgen import generate_case

ROW = {
    "age": 20, "score": 0.95, "level": 3,
    "vip": False, "active": True,
    "city": "beijing", "name": "alice", "nick": "alpha", "grade": 88,
}

# (expression, expected_bool) -- hand-computed against ROW.
GOLDEN = [
    ("age >= 18", True),
    ("age < 18", False),
    ("age >= 18 and vip == true", False),
    ("age >= 18 or vip == true", True),
    ("not (age < 18)", True),
    ("not not active", True),
    ("active and vip or age < 0", False),
    ("active or vip and age < 0", True),                 # and binds tighter
    ("age >= 18 or vip == true and score > 0.9", True),  # and binds tighter
    ("age < 18 or age > 60 and vip == true", False),
    ("active == true and city == 'beijing'", True),
    ("score > 0.5 and (vip == true or active == true)", True),
    ("null == null", True),
    ("null == 1", False),
    ("'b' >= 'a'", True),
    ("age == 20.0", True),
    ("name == nick", False),
]


class GoldenTests(unittest.TestCase):
    def test_golden(self):
        for source, expected in GOLDEN:
            with self.subTest(expr=source):
                self.assertEqual(evaluate(source, ROW), expected)


class PrecedenceStructureTests(unittest.TestCase):
    def test_or_lower_than_and(self):
        ast = parse("a == 1 or b == 2 and c == 3")
        self.assertEqual(ast[0], "or")
        self.assertEqual(ast[2][0], "and")

    def test_and_lower_than_not(self):
        ast = parse("not a == 1 and b == 2")
        self.assertEqual(ast[0], "and")
        self.assertEqual(ast[1][0], "not")

    def test_not_is_right_associative(self):
        ast = parse("not not a == 1")
        self.assertEqual(ast[0], "not")
        self.assertEqual(ast[1][0], "not")

    def test_left_associative_chain(self):
        ast = parse("a == 1 and b == 2 and c == 3")
        self.assertEqual(ast[0], "and")
        self.assertEqual(ast[1][0], "and")          # left child groups first
        self.assertEqual(ast[1][1][2][1], "a")
        self.assertEqual(ast[1][2][2][1], "b")
        self.assertEqual(ast[2][2][1], "c")

    def test_parentheses_override(self):
        ast = parse("(a == 1 or b == 2) and c == 3")
        self.assertEqual(ast[0], "and")
        self.assertEqual(ast[1][0], "or")


class TypeAndFieldTests(unittest.TestCase):
    def test_compare_number_string(self):
        with self.assertRaises(TypeMismatch) as ctx:
            evaluate("age > 'beijing'", ROW)
        self.assertEqual(ctx.exception.column, 5)

    def test_compare_number_bool(self):
        with self.assertRaises(TypeMismatch):
            evaluate("age == vip", ROW)

    def test_ordering_bools_rejected(self):
        with self.assertRaises(TypeMismatch):
            evaluate("vip < true", ROW)

    def test_logic_on_number(self):
        with self.assertRaises(TypeMismatch):
            evaluate("age and active", ROW)
        with self.assertRaises(TypeMismatch):
            evaluate("not age", ROW)

    def test_unknown_field_is_reported(self):
        with self.assertRaises(UnknownField) as ctx:
            evaluate("missing == 1", ROW)
        self.assertEqual(ctx.exception.name, "missing")
        self.assertEqual(ctx.exception.column, 1)

    def test_bare_identifier_is_type_error(self):
        with self.assertRaises(TypeMismatch):
            evaluate("age", ROW)

    def test_null_not_equal(self):
        self.assertIs(evaluate("null != age", ROW), True)


class MaliciousRejectionTests(unittest.TestCase):
    REJECT = [
        ("os.system('id')", ForbiddenConstruct),
        ("user.__class__", ForbiddenConstruct),
        ("user.name == 'x'", ForbiddenConstruct),
        ("rows[0] == 1", ForbiddenConstruct),
        ("(f)()", ForbiddenConstruct),
        ("age >= 18;", SyntaxFilterError),
    ]

    def test_all_rejected(self):
        for source, expected in self.REJECT:
            with self.subTest(expr=source):
                with self.assertRaises(expected):
                    compile_expr(source)

    def test_position_of_attribute(self):
        try:
            compile_expr("os.system('id')")
        except ForbiddenConstruct as err:
            self.assertEqual((err.line, err.column), (1, 3))
        else:
            self.fail()

    def test_position_of_call(self):
        try:
            compile_expr("f(1)")
        except ForbiddenConstruct as err:
            self.assertEqual((err.line, err.column), (1, 2))
        else:
            self.fail()

    def test_position_of_dunder(self):
        try:
            compile_expr("user.__class__")
        except ForbiddenConstruct as err:
            self.assertEqual((err.line, err.column), (1, 5))
        else:
            self.fail()

    def test_deep_nesting_rejected(self):
        source = "(" * 65 + "age == 1" + ")" * 65
        with self.assertRaises(ExpressionTooDeep) as ctx:
            compile_expr(source)
        self.assertEqual(ctx.exception.column, 65)

    def test_nesting_at_limit_accepted(self):
        source = "(" * 64 + "age == 1" + ")" * 64
        self.assertIs(evaluate(source, ROW), False)

    def test_not_chain_depth(self):
        with self.assertRaises(ExpressionTooDeep):
            compile_expr("not " * 65 + "active")

    def test_length_limit(self):
        with self.assertRaises(ExpressionTooLong):
            compile_expr("age == 1 or " + "active or " * 2000)

    def test_bad_character_and_literal(self):
        with self.assertRaises(SyntaxFilterError):
            compile_expr("age # 1")
        with self.assertRaises(MalformedLiteral):
            compile_expr("age == 1.")
        with self.assertRaises(MalformedLiteral):
            compile_expr("age == 'x")

    def test_chained_comparison_rejected(self):
        with self.assertRaises(SyntaxFilterError) as ctx:
            compile_expr("1 < 2 < 3")
        self.assertEqual(ctx.exception.column, 7)

    def test_empty_and_truncated(self):
        with self.assertRaises(SyntaxFilterError):
            compile_expr("")
        with self.assertRaises(SyntaxFilterError):
            compile_expr("age >=")


class ShortCircuitTests(unittest.TestCase):
    def test_skips_right_side(self):
        # If the right side were evaluated it would raise UnknownField.
        self.assertIs(evaluate("age < 18 and ghost == 1", ROW), False)
        self.assertIs(evaluate("age >= 18 or ghost == 1", ROW), True)


class DifferentialTests(unittest.TestCase):
    """Differential testing against Python's own operator semantics.

    Test-only oracle: expressions are generated by tests/diffgen.py from a
    safe, type-aware grammar; ``eval`` here is an isolated test oracle and
    is never used by the safefilter library itself.
    """

    def test_random_2000(self):
        rng = random.Random(20261005)
        for _ in range(2000):
            source, row, py_source = generate_case(rng)
            with self.subTest(expr=source):
                expected = eval(py_source, {"__builtins__": {}}, dict(row))
                self.assertEqual(evaluate(source, row), bool(expected))


if __name__ == "__main__":
    unittest.main()


class ColumnarTests(unittest.TestCase):
    def test_bind_columns_matches_row_mode(self):
        rows = [dict(ROW, age=a, vip=(a % 2 == 0)) for a in range(50)]
        columns = {k: [r[k] for r in rows] for k in rows[0]}
        for source, _ in GOLDEN:
            expr = compile_expr(source)
            count = expr.bind_columns(columns)(0, len(rows))
            expected = sum(1 for r in rows if expr.evaluate(r))
            self.assertEqual(count, expected, source)

    def test_unknown_column_rejected_at_bind(self):
        with self.assertRaises(UnknownField):
            compile_expr("ghost == 1").bind_columns({"age": [1, 2]})
