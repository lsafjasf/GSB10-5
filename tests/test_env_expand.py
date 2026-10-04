import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from env_expand import (
    CycleError,
    SyntaxExpandError,
    UndefinedVariableError,
    expand,
)


class TestPlainReference(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(expand("$HOME/bin", {"HOME": "/root"}), "/root/bin")

    def test_braced(self):
        self.assertEqual(expand("${HOME}bin", {"HOME": "/root"}), "/rootbin")

    def test_name_boundary(self):
        self.assertEqual(expand("$A1", {"A": "x", "A1": "y"}), "y")

    def test_adjacent(self):
        env = {"A": "1", "B": "2"}
        self.assertEqual(expand("$A$B", env), "12")
        self.assertEqual(expand("${A}${B}", env), "12")

    def test_empty_value_is_defined(self):
        self.assertEqual(expand("[$EMPTY]", {"EMPTY": ""}), "[]")

    def test_lone_dollar(self):
        self.assertEqual(expand("cost: $5 and $", {}), "cost: $5 and $")

    def test_top_level_brace_is_literal(self):
        self.assertEqual(expand("a}b", {}), "a}b")


class TestEscape(unittest.TestCase):
    def test_escaped_dollar(self):
        self.assertEqual(expand(r"\$HOME", {"HOME": "/root"}), "$HOME")

    def test_escaped_backslash(self):
        self.assertEqual(expand(r"\\$HOME", {"HOME": "/root"}), "\\/root")

    def test_backslash_before_other_char(self):
        self.assertEqual(expand(r"a\nb", {}), "a\\nb")

    def test_escaped_marker_never_reinterpreted(self):
        env = {"X": r"\$HOME", "HOME": "/root"}
        self.assertEqual(expand("$X", env), "$HOME")

    def test_value_with_dollar_not_rescanned(self):
        env = {"X": "$HOME", "HOME": "/root"}
        self.assertEqual(expand("$X", env), "/root")

    def test_concatenated_result_not_reinterpreted(self):
        # A 的值是字面 "$"，与 B 的展开结果拼成 "$HOME"：
        # 该拼接产物是替换后才出现的文本，不得再触发一轮替换。
        env = {"A": "$", "B": "HOME", "HOME": "/root"}
        self.assertEqual(expand("$A$B", env), "$HOME")


class TestNested(unittest.TestCase):
    def test_value_nesting(self):
        env = {"A": "$B/x", "B": "$C", "C": "leaf"}
        self.assertEqual(expand("$A", env), "leaf/x")

    def test_brace_nesting_name_composition(self):
        env = {"B": "X", "A_X": "hit"}
        self.assertEqual(expand("${A_${B}}", env), "hit")

    def test_innermost_first(self):
        env = {"INNER": "NAME", "PRE_NAME": "v"}
        self.assertEqual(expand("${PRE_${INNER}}", env), "v")

    def test_deep_chain(self):
        env = {f"V{i}": f"$V{i+1}" for i in range(50)}
        env["V50"] = "end"
        self.assertEqual(expand("$V0", env), "end")


class TestCycle(unittest.TestCase):
    def test_self_cycle(self):
        with self.assertRaises(CycleError) as ctx:
            expand("$A", {"A": "$A"})
        self.assertEqual(ctx.exception.cycle, ["A"])
        self.assertIn("A -> A", str(ctx.exception))

    def test_two_node_cycle(self):
        with self.assertRaises(CycleError) as ctx:
            expand("$A", {"A": "$B", "B": "$A"})
        self.assertEqual(ctx.exception.cycle, ["A", "B"])

    def test_cycle_path_reported(self):
        env = {"A": "$B", "B": "$C", "C": "$A"}
        with self.assertRaises(CycleError) as ctx:
            expand("$A", env)
        self.assertEqual(ctx.exception.cycle, ["A", "B", "C"])
        self.assertIn("A -> B -> C -> A", str(ctx.exception))

    def test_cycle_inside_value(self):
        env = {"TOP": "x=$A", "A": "$B", "B": "$A"}
        with self.assertRaises(CycleError) as ctx:
            expand("$TOP", env)
        self.assertEqual(ctx.exception.cycle, ["A", "B"])

    def test_shared_reference_is_not_a_cycle(self):
        env = {"A": "$B $C", "B": "$D", "C": "$D", "D": "leaf"}
        self.assertEqual(expand("$A", env), "leaf leaf")


class TestUndefined(unittest.TestCase):
    def test_error_by_default(self):
        with self.assertRaises(UndefinedVariableError) as ctx:
            expand("$NOPE", {})
        self.assertEqual(ctx.exception.name, "NOPE")

    def test_error_inside_value(self):
        with self.assertRaises(UndefinedVariableError) as ctx:
            expand("$A", {"A": "x=$MISSING"})
        self.assertEqual(ctx.exception.name, "MISSING")
        self.assertEqual(ctx.exception.referrer, "A")

    def test_empty_policy(self):
        self.assertEqual(expand("[$NOPE]", {}, undefined="empty"), "[]")

    def test_keep_policy(self):
        self.assertEqual(expand("$NOPE", {}, undefined="keep"), "${NOPE}")

    def test_undefined_vs_empty_value(self):
        with self.assertRaises(UndefinedVariableError):
            expand("$X", {})
        self.assertEqual(expand("$X", {"X": ""}), "")

    def test_bad_policy(self):
        with self.assertRaises(ValueError):
            expand("x", {}, undefined="bogus")


class TestSyntax(unittest.TestCase):
    def test_unclosed_brace(self):
        with self.assertRaises(SyntaxExpandError):
            expand("${A", {"A": "1"})

    def test_empty_braces(self):
        with self.assertRaises(SyntaxExpandError):
            expand("${}", {})

    def test_nested_empty_name(self):
        with self.assertRaises(SyntaxExpandError):
            expand("${${B}}", {"B": ""})


class TestTrace(unittest.TestCase):
    def test_trace_order(self):
        trace = []
        env = {"A": "$B/x", "B": "$C", "C": "leaf"}
        expand("$A", env, _trace=trace)
        kinds = [t[0] for t in trace]
        self.assertEqual(kinds, ["enter", "enter", "enter", "leave", "leave", "leave"])
        self.assertEqual(trace[0], ("enter", "A", "$B/x"))
        self.assertEqual(trace[-1], ("leave", "A", "leaf/x"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
