"""核心自测：词法、优先级/结合性、求值语义、错误分类、恶意拒绝与位置。"""

import sys
import unittest

sys.path.insert(0, ".")

import filterexpr as fx
from filterexpr import errors
from filterexpr.lexer import tokenize
from filterexpr.parser import DEFAULT_MAX_DEPTH, parse


class LexerTests(unittest.TestCase):
    def test_numbers_and_strings(self):
        toks = tokenize("12 12.5 .5 1e3 1.5e-2 'a\\nb' \"x\\u0041\"")
        vals = [t.value for t in toks]
        self.assertEqual(vals[:5], [12, 12.5, 0.5, 1000.0, 0.015])
        self.assertEqual(vals[5], "a\nb")
        self.assertEqual(vals[6], "xA")

    def test_keywords(self):
        types_ = [t.type for t in tokenize("and or not true false null x")]
        self.assertEqual(types_, ["AND", "OR", "NOT", "TRUE", "FALSE", "NULL", "IDENT", "EOF"])

    def test_illegal_char_points_to_position(self):
        with self.assertRaises(errors.FilterSyntaxError) as cm:
            tokenize("abc ;")
        self.assertEqual(cm.exception.col, 5)
        self.assertEqual(cm.exception.offset, 4)


class PrecedenceAndAssociativityTests(unittest.TestCase):
    def test_or_lower_than_and(self):
        # a or b and c  ==  a or (b and c)
        row = {"a": True, "b": False, "c": True}
        self.assertTrue(fx.compile("a or b and c").evaluate(row))

    def test_and_lower_than_not(self):
        # not a and b == (not a) and b
        row = {"a": False, "b": True}
        self.assertTrue(fx.compile("not a and b").evaluate(row))

    def test_cmp_binds_tighter_than_not(self):
        # not x == y == not (x == y)，而不是 (not x) == y
        row = {"x": 1, "y": 2}
        self.assertTrue(fx.compile("not x == y").evaluate(row))
        try:
            fx.compile("(not x) == y").evaluate(row)
            raised = False
        except errors.TypeMismatch:
            raised = True
        self.assertTrue(raised, "(not x) == y 必须触发类型不匹配")

    def test_left_associative_and_or(self):
        # ((a and b) and c) 与 (a or (b or c)) 在左结合下成立
        row = {"a": True, "b": True, "c": True}
        self.assertTrue(fx.compile("a and b and c").evaluate(row))
        self.assertTrue(fx.compile("a or b or c").evaluate(row))

    def test_chain_comparison_rejected_at_second_operator(self):
        with self.assertRaises(errors.FilterSyntaxError) as cm:
            fx.compile("a < b < c")
        self.assertEqual(cm.exception.source[cm.exception.offset], "<")
        self.assertEqual(cm.exception.col, 7)


class EvaluationSemanticsTests(unittest.TestCase):
    ROW = {"age": 30, "score": 88.5, "name": "alice", "active": True, "tag": None, "n": 0}

    def test_simple_comparisons(self):
        c = self.ROW
        self.assertTrue(fx.compile("age == 30").evaluate(c))
        self.assertTrue(fx.compile("age != 31").evaluate(c))
        self.assertTrue(fx.compile("age >= 18").evaluate(c))
        self.assertTrue(fx.compile("score < 100").evaluate(c))
        self.assertTrue(fx.compile("name == 'alice'").evaluate(c))
        self.assertTrue(fx.compile("active == true").evaluate(c))
        self.assertTrue(fx.compile("tag == null").evaluate(c))
        self.assertTrue(fx.compile("tag != 0").evaluate(c))

    def test_bool_is_not_number(self):
        # Python 里 True == 1；本语言刻意判定为不相等
        self.assertFalse(fx.compile("active == 1").evaluate(self.ROW))
        self.assertTrue(fx.compile("n == false").evaluate(self.ROW) is False)

    def test_ordering_requires_numbers(self):
        with self.assertRaises(errors.TypeMismatch):
            fx.compile("name > 'a'").evaluate(self.ROW)
        with self.assertRaises(errors.TypeMismatch):
            fx.compile("active < 1").evaluate(self.ROW)
        with self.assertRaises(errors.TypeMismatch):
            fx.compile("tag < 1").evaluate(self.ROW)

    def test_unary_neg(self):
        self.assertTrue(fx.compile("-age == -30").evaluate(self.ROW))
        with self.assertRaises(errors.TypeMismatch):
            fx.compile("-active").evaluate(self.ROW)

    def test_short_circuit_and_error_side(self):
        # 左侧为假时短路，不评估右侧（含缺失字段）
        self.assertFalse(
            fx.compile("active == false and missing == 1").evaluate(self.ROW)
        )
        with self.assertRaises(errors.FieldMissing):
            fx.compile("active == true and missing == 1").evaluate(self.ROW)

    def test_short_circuit_or(self):
        self.assertTrue(fx.compile("active == true or missing == 1").evaluate(self.ROW))
        with self.assertRaises(errors.FieldMissing):
            fx.compile("active == false or missing == 1").evaluate(self.ROW)

    def test_not_requires_bool(self):
        with self.assertRaises(errors.TypeMismatch):
            fx.compile("not age").evaluate(self.ROW)

    def test_top_level_must_be_bool(self):
        with self.assertRaises(errors.TypeMismatch):
            fx.compile("age").evaluate(self.ROW)


class ErrorClassificationTests(unittest.TestCase):
    def test_field_missing_category(self):
        with self.assertRaises(errors.FieldMissing) as cm:
            fx.compile("missing > 1").evaluate({})
        self.assertEqual(cm.exception.category, "field_missing")

    def test_type_mismatch_category(self):
        with self.assertRaises(errors.TypeMismatch) as cm:
            fx.compile("x > 1").evaluate({"x": "s"})
        self.assertEqual(cm.exception.category, "type_mismatch")

    def test_evaluate_many_never_silently_false(self):
        expr = fx.compile("age > 10")
        results, errs = expr.evaluate_many([{"age": 20}, {"age": "x"}, {}])
        self.assertEqual(results, [True, None, None])
        self.assertEqual([e[1] for e in errs], ["type_mismatch", "field_missing"])


class SecurityRejectionTests(unittest.TestCase):
    def _pointed_char(self, source, exc):
        return source[exc.offset]

    def test_function_call_various_forms(self):
        for source, ch in [
            ("len(x)", "("),
            ("__import__('os')", "("),
            ("f(x) == 1", "("),
            ("(x)()", "("),
        ]:
            with self.assertRaises(errors.FunctionCallNotAllowed) as cm:
                parse(source)
            self.assertEqual(cm.exception.category, "security", source)
            self.assertEqual(self._pointed_char(source, cm.exception), ch, source)

    def test_attribute_access(self):
        source = "a.b == 1"
        with self.assertRaises(errors.AttributeAccessNotAllowed) as cm:
            parse(source)
        self.assertEqual(cm.exception.category, "security")
        self.assertEqual(self._pointed_char(source, cm.exception), ".")

    def test_subscript(self):
        for source in ["a[0] == 1", "0][x"]:
            with self.assertRaises(errors.SubscriptNotAllowed) as cm:
                parse(source)
            self.assertEqual(cm.exception.category, "security", source)

    def test_paren_nesting_too_deep(self):
        depth = DEFAULT_MAX_DEPTH + 5
        source = "(" * depth + "x" + ")" * depth
        with self.assertRaises(errors.NestingTooDeep) as cm:
            parse(source)
        self.assertEqual(cm.exception.category, "security")
        self.assertEqual(self._pointed_char(source, cm.exception), "(")

    def test_not_chain_too_deep(self):
        source = "not " * (DEFAULT_MAX_DEPTH + 5) + "x == 1"
        with self.assertRaises(errors.NestingTooDeep):
            parse(source)

    def test_expression_too_long(self):
        source = "x and " + "y and " * 3400 + "z"
        with self.assertRaises(errors.ExpressionTooLong) as cm:
            parse(source)
        self.assertEqual(cm.exception.category, "security")

    def test_dot_after_literal_is_rejected(self):
        # "1..2" 被切成数字 1. 与 .2，构成残留 token，按语法错误拒绝
        with self.assertRaises(errors.FilterSyntaxError):
            parse("1..2 == 1")

    def test_misc_syntax_rejections(self):
        for source in [
            "",
            "   ",
            "age = 1",
            "x !",
            "x == 1;",
            "'unterminated",
            "age >=",
            "x == 1 2",
            "and x",
            "(x == 1",
            "x == 1)",
        ]:
            with self.assertRaises(errors.FilterSyntaxError, msg=source):
                parse(source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
