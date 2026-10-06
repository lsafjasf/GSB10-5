# -*- coding: utf-8 -*-
"""envexpand 的自测：普通 / 嵌套 / 循环 / 未定义 / 空值 / 转义。

运行：python3 -m unittest -v test_envexpand
      python3 test_envexpand.py
"""

import unittest

from envexpand import (
    EnvExpander,
    expand_vars,
    CircularReferenceError,
    UndefinedVariableError,
    UnmatchedBraceError,
    EmptyNameError,
)


def expand(template, env, **kw):
    return expand_vars(template, env, **kw)


class TestPlainReference(unittest.TestCase):
    def test_bare_name(self):
        self.assertEqual(expand("Hello $NAME!", {"NAME": "World"}), "Hello World!")

    def test_braced_reference(self):
        self.assertEqual(expand("${A}-${B}", {"A": "x", "B": "y"}), "x-y")

    def test_braced_adjacent(self):
        self.assertEqual(expand("${A}${B}", {"A": "ab", "B": "cd"}), "abcd")

    def test_no_reference(self):
        self.assertEqual(expand("plain text", {"A": "z"}), "plain text")

    def test_lone_dollar(self):
        # 结尾、跟标点、跟数字开头：都按字面量输出
        self.assertEqual(expand("cost is 5$", {}), "cost is 5$")
        self.assertEqual(expand("$", {}), "$")
        self.assertEqual(expand("a $ b", {}), "a $ b")
        self.assertEqual(expand("$1", {}), "$1")

    def test_greedy_name(self):
        # 裸名贪婪吞掉下划线/数字；需要边界时用 ${}
        self.assertEqual(expand("$FOO_BAR", {"FOO_BAR": "v"}), "v")
        self.assertEqual(expand("${A}_${B}", {"A": "1", "B": "2"}), "1_2")


class TestEscape(unittest.TestCase):
    def test_dollar_dollar(self):
        self.assertEqual(expand("price: $$5", {}), "price: $5")
        self.assertEqual(expand("$$$$", {}), "$$")

    def test_escaped_braces(self):
        self.assertEqual(expand("$${A}", {"A": "x"}), "${A}")
        self.assertEqual(expand("$${", {}), "${")

    def test_dollar_in_value_not_re_expanded(self):
        # 值是模板：$INJECT 的值 "$SECRET" 被展开一次 -> "leaked"（make 式递归）
        env = {"INJECT": "$SECRET", "SECRET": "leaked"}
        self.assertGreater(len(env), 0)
        self.assertEqual(expand("$INJECT", env), "leaked")
        # 关键防坑：值里转义后的 $ 是字面量，替换进结果后绝不二次解释
        env2 = {"X": "$${B}", "B": "leak"}
        self.assertEqual(expand("$X", env2), "${B}")
        env3 = {"Y": "$$HOME", "HOME": "/root"}
        self.assertEqual(expand("$Y", env3), "$HOME")

    def test_dollar_dollar_in_value_is_literal(self):
        # 值作为模板处理，其中的 $$ 同样按转义规则折叠为一个字面量 $
        env = {"X": "a$$b"}
        self.assertEqual(expand("$X", env), "a$b")


class TestNestedReference(unittest.TestCase):
    def test_simple_indirection(self):
        self.assertEqual(expand("${${A}}", {"A": "K", "K": "deep"}), "deep")

    def test_nested_bare_name(self):
        # ${A_$I}: 内层 $I -> 2，外层查 A_2
        env = {"I": "2", "A_2": "hit"}
        self.assertEqual(expand("${A_$I}", env), "hit")

    def test_three_level_nesting(self):
        env = {"L1": "L2", "L2": "L3", "L3": "value"}
        self.assertEqual(expand("${${${L1}}}", env), "value")

    def test_nested_with_literal_context(self):
        env = {"PREFIX": "HOME", "HOME": "/root"}
        self.assertEqual(expand("path=${${PREFIX}}/bin", env), "path=/root/bin")

    def test_nested_undefined_inner_raises(self):
        with self.assertRaises(UndefinedVariableError) as cm:
            expand("${${MISSING}}", {})
        self.assertEqual(cm.exception.name, "MISSING")

    def test_nested_empty_inner_name(self):
        with self.assertRaises(EmptyNameError):
            expand("${${E}}", {"E": ""})

    def test_nested_inner_empty_vs_undefined(self):
        # 内层名字表达式引用了空值变量：内层结果为空 -> EmptyNameError
        with self.assertRaises(EmptyNameError):
            expand("${${E}}", {"E": ""})
        # 外层变量值为空：正常得到空串
        self.assertEqual(expand("${E}", {"E": ""}), "")

    def test_unmatched_brace(self):
        with self.assertRaises(UnmatchedBraceError):
            expand("${A", {"A": "x"})
        with self.assertRaises(UnmatchedBraceError):
            expand("${${A}", {"A": "x"})

    def test_escape_inside_braces_not_nesting(self):
        # 名字表达式里的 $$ 先折叠为字面量 $，再参与拼名
        with self.assertRaises(UndefinedVariableError):
            expand("${$$}", {})  # $$ -> 名字 "$"，未定义
        env2 = {"$K": "weird"}
        self.assertEqual(expand("${$$K}", env2), "weird")


class TestValueIsTemplate(unittest.TestCase):
    def test_value_reference(self):
        env = {"A": "$B", "B": "base"}
        self.assertEqual(expand("pre/$A/post", env), "pre/base/post")

    def test_value_chain(self):
        env = {"A": "${B}!${B}", "B": "$C", "C": "ok"}
        self.assertEqual(expand("$A", env), "ok!ok")

    def test_empty_value_is_not_undefined(self):
        env = {"E": ""}
        self.assertEqual(expand("[$E]", env), "[]")
        self.assertEqual(expand("[${E}]", env), "[]")

    def test_undefined_default_raise(self):
        with self.assertRaises(UndefinedVariableError):
            expand("$NOPE", {})
        with self.assertRaises(UndefinedVariableError):
            expand("${NOPE}", {})

    def test_undefined_empty_policy(self):
        self.assertEqual(expand("[$MISSING]", {}, on_undefined="empty"), "[]")
        self.assertEqual(expand("a$MISSING b", {}, on_undefined="empty"), "a b")

    def test_undefined_keep_policy(self):
        self.assertEqual(expand("a${X}b", {}, on_undefined="keep"), "a${X}b")
        self.assertEqual(expand("$X-$Y", {"X": "1"}, on_undefined="keep"), "1-${Y}")

    def test_undefined_callable_policy(self):
        env = {"X": "x"}
        self.assertEqual(
            expand("$X/$MISS", env, on_undefined=lambda name: "<%s>" % name),
            "x/<MISS>",
        )

    def test_bad_policy_constructor(self):
        with self.assertRaises(ValueError):
            EnvExpander({}, on_undefined="bogus")


class TestCircularReference(unittest.TestCase):
    def test_self_reference(self):
        try:
            expand("$A", {"A": "$A"})
        except CircularReferenceError as e:
            self.assertEqual(e.cycle, ["A"])
        else:
            self.fail("应当检测到自引用循环")

    def test_two_node_cycle(self):
        try:
            expand("${X}", {"X": "$Y", "Y": "${X}"})
        except CircularReferenceError as e:
            self.assertEqual(set(e.cycle), {"X", "Y"})
            self.assertEqual(len(e.cycle), 2)
            # 路径完整可核对
            self.assertEqual(e.path[0], "X")
        else:
            self.fail("应当检测到 X/Y 互相引用")

    def test_three_node_cycle(self):
        env = {"A": "$B", "B": "$C", "C": "${A}-end"}
        try:
            expand("$A", env)
        except CircularReferenceError as e:
            self.assertEqual(set(e.cycle), {"A", "B", "C"})
            self.assertEqual(len(e.cycle), 3)
            self.assertEqual(e.path, ["A", "B", "C", "A"])
        else:
            self.fail("应当检测到三节点循环")

    def test_cycle_with_lead_in(self):
        # 引用起点不在环上：G -> H -> I -> H；环是 H -> I -> H
        env = {"G": "$H", "H": "$I", "I": "${H}"}
        try:
            expand("$G", env)
        except CircularReferenceError as e:
            self.assertEqual(e.cycle, ["H", "I"])
            self.assertEqual(e.path, ["G", "H", "I", "H"])
        else:
            self.fail("应当检测到尾部循环")

    def test_same_name_repeated_dag_is_not_cycle(self):
        # DAG 中同一变量被引用多次不是循环
        env = {"A": "a$B", "B": "b", "C": "$A$A"}
        self.assertEqual(expand("$C", env), "abab")

    def test_cycle_message_names_ring(self):
        try:
            expand("${P}", {"P": "${Q}", "Q": "${R}", "R": "${P}"})
        except CircularReferenceError as e:
            msg = str(e)
            for name in ("P", "Q", "R"):
                self.assertIn(name, msg)
        else:
            self.fail()

    def test_cycle_in_nested_name_expr(self):
        # 名字表达式里的嵌套引用同样受环检测保护
        env = {"A": "$B", "B": "$A"}
        with self.assertRaises(CircularReferenceError):
            expand("${${A}}", env)


class TestTrace(unittest.TestCase):
    def test_trace_records_steps(self):
        events = []
        ex = EnvExpander({"A": "v", "T": "$A"}, trace=lambda d, ev, *a: events.append((d, ev, a)))
        ex.expand("$T")
        kinds = [ev for _, ev, _ in events]
        self.assertIn("ref", kinds)
        self.assertIn("value", kinds)

    def test_memoization_uses_cache_event(self):
        events = []
        ex = EnvExpander({"B": "b", "A": "$B$B"}, trace=lambda d, ev, *a: events.append((d, ev, a)))
        self.assertEqual(ex.expand("$A"), "bb")
        self.assertTrue(any(ev == "cached" and args[0] == "B" for _, ev, args in events))


if __name__ == "__main__":
    unittest.main(verbosity=2)
