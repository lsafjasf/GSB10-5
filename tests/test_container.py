"""自测：无依赖 / 线性链 / 循环依赖 / 延迟注入 / 构造失败回收 / 顺序确定性。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from di import (
    CircularDependencyError,
    ConstructionError,
    Container,
    LazyCycleError,
    LazyRef,
    MissingDependencyError,
    lazy,
)


def kinds(container):
    return [e.kind for e in container.trace.events]


class TestNoDependency(unittest.TestCase):
    def test_single_component(self):
        c = Container()
        c.register("a", lambda: "A")
        self.assertEqual(c.resolve("a"), "A")
        self.assertEqual(kinds(c),
                         ["resolve_begin", "visit", "construct_begin",
                          "construct_end", "resolve_end"])

    def test_singleton_cached(self):
        c = Container()
        calls = []
        c.register("a", lambda: calls.append(1) or "A")
        c.resolve("a")
        c.resolve("a")
        self.assertEqual(len(calls), 1)
        self.assertIn("cached", kinds(c))


class TestLinearChain(unittest.TestCase):
    def test_postorder(self):
        c = Container()
        order = []
        for name, deps in [("a", ("b",)), ("b", ("c",)), ("c", ())]:
            c.register(name, lambda *a, n=name: order.append(n) or n, deps=deps)
        c.resolve("a")
        self.assertEqual(order, ["c", "b", "a"])  # 后序：依赖先于使用者

    def test_diamond_built_once(self):
        c = Container()
        order = []
        c.register("top", lambda l, r: order.append("top"), deps=("l", "r"))
        c.register("l", lambda b: order.append("l"), deps=("b",))
        c.register("r", lambda b: order.append("r"), deps=("b",))
        c.register("b", lambda: order.append("b"))
        c.resolve("top")
        self.assertEqual(order, ["b", "l", "r", "top"])
        self.assertEqual(order.count("b"), 1)

    def test_dep_order_follows_declaration(self):
        c = Container()
        order = []
        c.register("root", lambda x, y: None, deps=("x", "y"))
        c.register("x", lambda: order.append("x"))
        c.register("y", lambda: order.append("y"))
        c.resolve("root")
        self.assertEqual(order, ["x", "y"])


class TestCycles(unittest.TestCase):
    def test_self_loop(self):
        c = Container()
        c.register("a", lambda a: None, deps=("a",))
        with self.assertRaises(CircularDependencyError) as ctx:
            c.resolve("a")
        self.assertEqual(ctx.exception.report.members, ["a"])
        self.assertEqual(ctx.exception.report.cycles[0].example, ["a", "a"])

    def test_two_node_cycle(self):
        c = Container()
        c.register("a", lambda b: None, deps=("b",))
        c.register("b", lambda a: None, deps=("a",))
        with self.assertRaises(CircularDependencyError) as ctx:
            c.resolve("a")
        report = ctx.exception.report
        self.assertEqual(sorted(report.members), ["a", "b"])
        ex = report.cycles[0].example
        self.assertEqual(ex[0], ex[-1])
        self.assertEqual(sorted(set(ex)), ["a", "b"])

    def test_all_cycle_members_reported(self):
        # 环1: a-b-c-a；环2: d-e-d；无关分支 f。root 可达全部。
        c = Container()
        c.register("root", lambda a, d, f: None, deps=("a", "d", "f"))
        c.register("a", lambda b: None, deps=("b",))
        c.register("b", lambda cc: None, deps=("c",))
        c.register("c", lambda a: None, deps=("a",))
        c.register("d", lambda e: None, deps=("e",))
        c.register("e", lambda d: None, deps=("d",))
        c.register("f", lambda: None)
        with self.assertRaises(CircularDependencyError) as ctx:
            c.resolve("root")
        report = ctx.exception.report
        self.assertEqual(sorted(report.members), ["a", "b", "c", "d", "e"])
        self.assertEqual(len(report.cycles), 2)
        self.assertNotIn("f", report.members)
        self.assertNotIn("root", report.members)

    def test_cycle_fails_before_any_construction(self):
        c = Container()
        built = []
        c.register("a", lambda b: built.append("a"), deps=("b",))
        c.register("b", lambda a: built.append("b"), deps=("a",))
        with self.assertRaises(CircularDependencyError):
            c.resolve("a")
        self.assertEqual(built, [])  # fail-fast：一个都没构造


class TestLazyInjection(unittest.TestCase):
    def test_lazy_breaks_cycle(self):
        c = Container()
        c.register("a", lambda b_ref: ("A", b_ref), deps=(lazy("b"),))
        c.register("b", lambda a: ("B", a), deps=("a",))
        inst_a = c.resolve("a")            # 不报错：lazy 边不参与拓扑排序
        a_val, b_ref = inst_a
        self.assertEqual(a_val, "A")
        self.assertIsInstance(b_ref, LazyRef)
        self.assertFalse(b_ref.resolved)   # 构造期未取值
        b_val, a_of_b = b_ref.get()        # 第一次取值才真正解析 b
        self.assertEqual(b_val, "B")
        self.assertIs(a_of_b, inst_a)      # b 拿到的是同一个 a 实例
        self.assertTrue(b_ref.resolved)

    def test_lazy_not_constructed_until_accessed(self):
        c = Container()
        built = []
        c.register("a", lambda ref: ref, deps=(lazy("heavy"),))
        c.register("heavy", lambda: built.append("heavy") or "H")
        c.resolve("a")
        self.assertEqual(built, [])        # 解析 a 时 heavy 未构造
        self.assertIn("lazy_inject", kinds(c))
        self.assertNotIn("lazy_access", kinds(c))

    def test_lazy_access_trace(self):
        c = Container()
        c.register("a", lambda ref: ref, deps=(lazy("b"),))
        c.register("b", lambda: "B")
        ref = c.resolve("a")
        self.assertEqual(ref.get(), "B")
        k = kinds(c)
        self.assertLess(k.index("lazy_inject"), k.index("lazy_access"))
        self.assertEqual(ref.get(), "B")   # 第二次取值走缓存
        self.assertEqual(kinds(c).count("lazy_access"), 1)

    def test_lazy_access_during_construction_reports_dynamic_cycle(self):
        c = Container()
        c.register("a", lambda b_ref: b_ref.get(), deps=(lazy("b"),))
        c.register("b", lambda a: "B", deps=("a",))
        with self.assertRaises(LazyCycleError) as ctx:
            c.resolve("a")
        self.assertEqual(ctx.exception.cycle, ["a", "b", "a"])


class TestFailureRollback(unittest.TestCase):
    def test_rollback_reverse_order(self):
        c = Container()
        disposed = []
        c.register("a", lambda b: "A", deps=("b",),
                   disposer=lambda i: disposed.append("a"))
        c.register("b", lambda c_: "B", deps=("c",),
                   disposer=lambda i: disposed.append("b"))
        c.register("c", lambda: "C", disposer=lambda i: disposed.append("c"))
        def boom(_dep):
            raise RuntimeError("boom")
        c.register("d", boom, deps=("a",))
        with self.assertRaises(ConstructionError) as ctx:
            c.resolve("d")
        self.assertIsInstance(ctx.exception.original, RuntimeError)
        self.assertEqual(disposed, ["a", "b", "c"])  # 逆构造顺序回收
        # 实例已从容器移除，状态复位
        for n in ("a", "b", "c", "d"):
            self.assertNotIn(n, c._instances)
        self.assertEqual(c._built_order, [])

    def test_disposer_error_does_not_stop_cleanup(self):
        c = Container()
        disposed = []
        c.register("x", lambda: "X",
                   disposer=lambda i: (_ for _ in ()).throw(ValueError("bad dispose")))
        c.register("y", lambda: "Y", disposer=lambda i: disposed.append("y"))
        c.register("z", lambda: (_ for _ in ()).throw(RuntimeError("z fails")),
                   deps=("x", "y"))
        with self.assertRaises(ConstructionError):
            c.resolve("z")
        self.assertEqual(disposed, ["y"])  # x 的 disposer 抛错，y 仍被回收
        self.assertEqual(c._instances, {})

    def test_container_usable_after_failure(self):
        c = Container()
        c.register("ok", lambda: "OK")
        c.register("bad", lambda: (_ for _ in ()).throw(RuntimeError()), deps=("ok",))
        with self.assertRaises(ConstructionError):
            c.resolve("bad")
        self.assertEqual(c.resolve("ok"), "OK")  # 容器仍可正常使用

    def test_rollback_only_touches_current_resolution(self):
        c = Container()
        disposed = []
        c.register("base", lambda: "BASE", disposer=lambda i: disposed.append("base"))
        c.resolve("base")                          # 上一次成功解析的实例
        c.register("leaf", lambda: "LEAF", disposer=lambda i: disposed.append("leaf"))
        c.register("bad", lambda l: (_ for _ in ()).throw(RuntimeError()),
                   deps=("leaf", "base"))
        with self.assertRaises(ConstructionError):
            c.resolve("bad")
        self.assertEqual(disposed, ["leaf"])       # base 是旧实例，不被回收
        self.assertEqual(c.resolve("base"), "BASE")


class TestMissingDependency(unittest.TestCase):
    def test_missing_dep(self):
        c = Container()
        c.register("a", lambda b: None, deps=("ghost",))
        with self.assertRaises(MissingDependencyError):
            c.resolve("a")


class TestDeterminism(unittest.TestCase):
    def test_same_graph_same_trace(self):
        def build():
            c = Container()
            c.register("root", lambda a, b: None, deps=("a", "b"))
            c.register("a", lambda c_: None, deps=("c",))
            c.register("b", lambda c_: None, deps=("c",))
            c.register("c", lambda: None)
            c.resolve("root")
            return [(e.kind, e.name, e.detail) for e in c.trace.events]
        self.assertEqual(build(), build())


if __name__ == "__main__":
    unittest.main(verbosity=2)
