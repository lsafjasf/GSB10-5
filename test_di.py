"""test_di.py — di 容器自测（unittest，仅标准库）。

覆盖：无依赖 / 线性链 / 菱形 / 循环依赖（全成员定位）/ 自环 /
      多环 / 延迟注入（取值时机 + 解环）/ 构造失败逆序回收 / 单例缓存。
"""

import unittest

from di import (Container, Tracer, CycleError, ConstructionError,
                LazyRef, UnknownComponentError)


class TestNoDeps(unittest.TestCase):
    def test_single_component(self):
        c = Container()
        c.register("cfg", lambda: {"v": 1})
        self.assertEqual(c.resolve("cfg"), {"v": 1})
        self.assertIs(c.resolve("cfg"), c.resolve("cfg"))  # 单例
        kinds = [e[1] for e in c.tracer.events]
        self.assertIn("ORDER", kinds)
        self.assertIn("CREATE", kinds)

    def test_unknown(self):
        with self.assertRaises(UnknownComponentError):
            Container().resolve("nope")

    def test_duplicate_register(self):
        c = Container()
        c.register("a", lambda: 1)
        with self.assertRaises(Exception):
            c.register("a", lambda: 2)


class TestLinearChain(unittest.TestCase):
    def test_chain_order(self):
        c = Container()
        c.register("c", lambda b: b + "C", deps={"b": "b"})
        c.register("b", lambda a: a + "B", deps={"a": "a"})
        c.register("a", lambda: "A")
        order = c.resolution_order(["c"])
        self.assertEqual(order, ["a", "b", "c"])
        self.assertEqual(c.resolve("c"), "ABC")

    def test_linear_1000_no_recursion(self):
        # 深链必须走迭代式算法，不能爆栈
        c = Container()
        c.register("n0", lambda: 0)
        for i in range(1, 1000):
            c.register(f"n{i}",
                       (lambda prev: lambda dep: dep + 1)(f"n{i-1}"),
                       deps={"dep": f"n{i-1}"})
        self.assertEqual(c.resolve("n999"), 999)

    def test_diamond_sibling_declaration_order(self):
        c = Container()
        order_log = []
        def make(name):
            def f(**_kwargs):
                order_log.append(name)
                return name
            return f
        c.register("top",
                   lambda left, right: left + right,
                   deps={"left": "left", "right": "right"})
        c.register("right", make("right"), deps={"base": "base"})
        c.register("left", make("left"), deps={"base": "base"})
        c.register("base", make("base"))
        order = c.resolution_order(["top"])
        # base 最前；left/right 按 top 的 deps 声明顺序
        self.assertEqual(order, ["base", "left", "right", "top"])
        c.resolve("top")
        self.assertEqual(order_log, ["base", "left", "right"])


class TestCycle(unittest.TestCase):
    def test_cycle_full_members(self):
        c = Container()
        c.register("a", lambda b: b, deps={"b": "b"})
        c.register("b", lambda cc: cc, deps={"cc": "c"})
        c.register("c", lambda a: a, deps={"a": "a"})
        # D 依赖 B，但不在环上
        c.register("d", lambda b: b, deps={"b": "b"})
        cycles = c.find_cycles(["d"])
        self.assertEqual(len(cycles), 1)
        self.assertEqual(cycles[0]["members"], ["a", "b", "c"])
        self.assertEqual(cycles[0]["path"], ["a", "b", "c", "a"])
        with self.assertRaises(CycleError) as ctx:
            c.resolve("a")
        self.assertEqual(ctx.exception.sccs, [["a", "b", "c"]])
        report = ctx.exception.report
        self.assertIn("a", report) and self.assertIn("c", report)
        self.assertIn("a -> b -> c -> a", report)

    def test_self_loop(self):
        c = Container()
        c.register("x", lambda x: x, deps={"x": "x"})
        cycles = c.find_cycles()
        self.assertEqual(cycles[0]["members"], ["x"])
        self.assertEqual(cycles[0]["path"], ["x", "x"])

    def test_two_disjoint_cycles(self):
        c = Container()
        c.register("a", lambda b: b, deps={"b": "b"})
        c.register("b", lambda a: a, deps={"a": "a"})
        c.register("p", lambda q: q, deps={"q": "q"})
        c.register("q", lambda p: p, deps={"p": "p"})
        cycles = c.find_cycles()
        self.assertEqual(len(cycles), 2)
        members = sorted(tuple(x["members"]) for x in cycles)
        self.assertEqual(members, [("a", "b"), ("p", "q")])

    def test_no_false_positive(self):
        c = Container()
        c.register("a", lambda b: b, deps={"b": "b"})
        c.register("b", lambda: 1)
        self.assertEqual(c.find_cycles(), [])
        self.assertEqual(c.cycle_report(), "")


class TestLazyInjection(unittest.TestCase):
    def test_lazy_resolves_only_on_get(self):
        c = Container()
        build_log = []
        c.register("dep", lambda: build_log.append("dep-built") or "D")
        c.register("user",
                   lambda d: (build_log.append("user-built"), d)[1],
                   lazy={"d": "dep"})
        user = c.resolve("user")
        self.assertIsInstance(user, LazyRef)
        self.assertFalse(user.resolved)
        self.assertEqual(build_log, ["user-built"])  # 注入时未取值
        self.assertEqual(user.get(), "D")
        self.assertTrue(user.resolved)
        self.assertEqual(build_log, ["user-built", "dep-built"])
        user.get()  # 第二次命中缓存
        kinds = [e[1] for e in c.tracer.events]
        self.assertIn("INJECT-LAZY", kinds)
        self.assertIn("RESOLVE-LAZY", kinds)
        self.assertIn("LAZY-CACHED", kinds)

    def test_lazy_breaks_constructor_cycle(self):
        # 经典：A 需要 B，B 构造时需要 A；B 对 A 延迟注入，在自己的方法里再取值
        c = Container()
        class A:
            def __init__(self, b_ref):
                self.b_ref = b_ref
            def use_b(self):
                return self.b_ref.get().value
        class B:
            def __init__(self, a_ref):
                self.a_ref = a_ref
                self.value = "B-value"
            def use_a(self):
                return self.a_ref.get()
        c.register("a", A, lazy={"b_ref": "b"})
        c.register("b", B, lazy={"a_ref": "a"})
        a = c.resolve("a")
        self.assertEqual(a.use_b(), "B-value")   # 此时才解析 b
        b = a.b_ref.get()
        self.assertIs(a.b_ref.get(), c.resolve("b"))
        self.assertIs(b.use_a(), a)

    def test_lazy_does_not_participate_in_cycle_check(self):
        # a 急切依赖 b，b 仅延迟依赖 a：不应报环
        c = Container()
        c.register("a", lambda b: ("a", b), deps={"b": "b"})
        c.register("b", lambda a_ref: ("b", a_ref), lazy={"a_ref": "a"})
        order = c.resolution_order(["a"])
        self.assertEqual(order, ["b", "a"])
        name, b_tuple = c.resolve("a")
        self.assertEqual(name, "a")
        b_name, a_ref = b_tuple
        self.assertEqual(b_name, "b")
        self.assertIsInstance(a_ref, LazyRef)
        self.assertIs(a_ref.get(), c.resolve("a"))


class TestCleanup(unittest.TestCase):
    def test_reverse_order_cleanup_on_failure(self):
        c = Container()
        closed = []
        class Res:
            def __init__(self, name):
                self.name = name
            def close(self):
                closed.append(self.name)
        c.register("a",
                   lambda b: Res("a"),
                   deps={"b": "b"})
        c.register("b",
                   lambda dep: Res("b"),
                   deps={"dep": "base"})
        c.register("base", lambda: Res("base"))

        def boom(dep):
            raise RuntimeError("factory boom")
        c.register("boom", boom, deps={"dep": "a"})
        with self.assertRaises(ConstructionError) as ctx:
            c.resolve("boom")
        # 已构造 base, b, a；逆序回收
        self.assertEqual(ctx.exception.cleaned, ["a", "b", "base"])
        self.assertEqual(closed, ["a", "b", "base"])
        # 失败后缓存中不留残骸
        self.assertEqual(c._singletons, {})
        kinds = [e[1] for e in c.tracer.events]
        self.assertEqual(kinds.count("CLEANUP"), 3)
        self.assertIn("FAIL", kinds)

    def test_custom_disposer(self):
        c = Container()
        disposed = []
        c.register("a", lambda: object(),
                   disposer=lambda inst: disposed.append("a"))
        with self.assertRaises(ConstructionError):
            c.register("b", lambda a: (_ for _ in ()).throw(ValueError("x")),
                       deps={"a": "a"})
            c.resolve("b")
        self.assertEqual(disposed, ["a"])

    def test_partial_failure_does_not_touch_existing_singletons(self):
        c = Container()
        c.register("old", lambda: "old")
        c.resolve("old")
        c.register("new",
                   lambda old: (_ for _ in ()).throw(RuntimeError("x")),
                   deps={"old": "old"})
        with self.assertRaises(ConstructionError):
            c.resolve("new")
        self.assertEqual(c.resolve("old"), "old")  # 既有单例不受影响


class TestTrace(unittest.TestCase):
    def test_trace_is_sequential_and_rendered(self):
        c = Container()
        c.register("b", lambda: 2)
        c.register("a", lambda b: b * 2, deps={"b": "b"})
        c.resolve("a")
        text = c.tracer.render()
        self.assertIn("[001]", text)
        self.assertTrue(text.splitlines()[0].startswith("[001] ORDER"))

    def test_fresh_tracer(self):
        t = Tracer()
        t.log("X", "m")
        self.assertIn("X", t.render())
        t.clear()
        self.assertEqual(t.events, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
