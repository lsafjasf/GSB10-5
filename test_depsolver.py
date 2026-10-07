"""depsolver 自测：完全线性、宽并行、成环、含可选依赖、弱依赖、校验、基准规模。"""

import time
import unittest

from depsolver import DepGraph, break_plan_detail, derive, verify


def layers_of(plan):
    return [sorted(s) for s in plan.steps]


class LinearChainTest(unittest.TestCase):
    """A <- B <- C <- D（D 依赖 C 依赖 B 依赖 A），必须每层一个、顺序唯一。"""

    def setUp(self):
        self.g = DepGraph(strong={"A": [], "B": ["A"], "C": ["B"], "D": ["C"]})
        self.plan = derive(self.g)

    def test_layers(self):
        self.assertEqual(layers_of(self.plan), [["A"], ["B"], ["C"], ["D"]])

    def test_no_cycles_no_cuts(self):
        self.assertEqual(self.plan.cycles, [])
        self.assertEqual(self.plan.cut_edges, [])

    def test_verify_ok(self):
        ver = verify(self.g, self.plan)
        self.assertTrue(ver.ok)
        self.assertEqual(ver.strong_edges_total, 3)
        self.assertEqual(ver.strong_edges_ordered, 3)


class WideParallelTest(unittest.TestCase):
    """一个底座 + 100 个互不依赖的服务，全部落在第 2 层可并行。"""

    def setUp(self):
        strong = {"base": []}
        for i in range(100):
            strong[f"svc-{i}"] = ["base"]
        self.g = DepGraph(strong=strong)
        self.plan = derive(self.g)

    def test_two_layers(self):
        layers = layers_of(self.plan)
        self.assertEqual(len(layers), 2)
        self.assertEqual(layers[0], ["base"])
        self.assertEqual(len(layers[1]), 100)

    def test_layer_has_no_internal_strong_edges(self):
        ver = verify(self.g, self.plan)
        self.assertEqual(ver.layer_internal_edges, [])
        self.assertTrue(ver.ok)


class CycleTest(unittest.TestCase):
    """X -> Y -> Z -> X 三环，必须割一条边并给出拆解方案。"""

    def setUp(self):
        self.g = DepGraph(strong={"X": ["Z"], "Y": ["X"], "Z": ["Y"]})
        self.plan = derive(self.g)
        self.ver = verify(self.g, self.plan)

    def test_cycle_detected(self):
        self.assertEqual(self.plan.cycles, [["X", "Y", "Z"]])

    def test_exactly_one_cut(self):
        # 3 节点简单环，割边数上界 |SCC|-1 = 2，最优为 1
        self.assertEqual(len(self.plan.cut_edges), 1)

    def test_remaining_edges_ordered(self):
        self.assertTrue(self.ver.ok)
        self.assertEqual(self.ver.strong_edges_cut, 1)
        self.assertEqual(self.ver.strong_edges_ordered, 2)

    def test_break_plan_names_affected(self):
        detail = break_plan_detail(self.plan)
        self.assertEqual(detail["cut_count"], 1)
        self.assertEqual(len(detail["phases"]), 2)
        self.assertEqual(len(detail["affected_components"]), 2)
        self.assertEqual(len(detail["components_to_redeploy" if False else "phases"][1]["components_to_redeploy"]), 1)


class TwoInterlockingCyclesTest(unittest.TestCase):
    """两个共享节点的环，割边要能同时解开。"""

    def setUp(self):
        # A->B->A 与 B->C->B 共点 B
        self.g = DepGraph(strong={"A": ["B"], "B": ["A", "C"], "C": ["B"]})
        self.plan = derive(self.g)
        self.ver = verify(self.g, self.plan)

    def test_one_scc(self):
        self.assertEqual(self.plan.cycles, [["A", "B", "C"]])

    def test_cuts_break_all_cycles(self):
        self.assertTrue(self.ver.ok)
        self.assertGreaterEqual(len(self.plan.cut_edges), 1)
        self.assertLessEqual(len(self.plan.cut_edges), 2)  # 上界 |SCC|-1


class OptionalDepsTest(unittest.TestCase):
    """可选依赖不参与排序，也不制造环。"""

    def setUp(self):
        self.g = DepGraph(
            strong={"app": ["db"], "db": []},
            optional={"app": ["cache"], "cache": ["app"]},  # 可选“环”不算环
        )
        self.plan = derive(self.g)

    def test_optional_not_in_cycles(self):
        self.assertEqual(self.plan.cycles, [])
        self.assertEqual(self.plan.cut_edges, [])

    def test_optional_recorded(self):
        self.assertEqual(
            self.plan.optional_edges, [("app", "cache"), ("cache", "app")]
        )

    def test_ordering_only_by_strong(self):
        idx = self.plan.index()
        self.assertLess(idx["db"], idx["app"])
        self.assertTrue(verify(self.g, self.plan).ok)


class WeakDepsTest(unittest.TestCase):
    def test_satisfied_when_consistent(self):
        g = DepGraph(strong={"a": [], "b": ["a"]}, weak={"b": ["a"]})
        plan = derive(g)
        self.assertEqual(plan.weak_satisfied, [("a", "b")])
        self.assertEqual(plan.weak_deferred, [])

    def test_deferred_when_conflicting(self):
        # 强依赖要求 b 在 a 前，弱依赖希望 a 在 b 前：弱依赖让位
        g = DepGraph(strong={"a": ["b"], "b": []}, weak={"b": ["a"]})
        plan = derive(g)
        self.assertEqual(plan.weak_deferred, [("a", "b")])
        self.assertEqual(plan.weak_satisfied, [])
        self.assertTrue(verify(g, plan).ok)  # 弱依赖冲突不影响强依赖校验


class MissingNodeTest(unittest.TestCase):
    def test_undeclared_dependency_is_placed_and_reported(self):
        g = DepGraph(strong={"app": ["ghost"]})
        plan = derive(g)
        self.assertIn("ghost", plan.missing_nodes)
        idx = plan.index()
        self.assertLess(idx["ghost"], idx["app"])
        self.assertTrue(verify(g, plan).ok)


class BenchScaleTest(unittest.TestCase):
    """1 万组件规模：推导应秒级完成且校验通过。"""

    def test_10k_components(self):
        from depsolver import _gen_bench_graph

        g = _gen_bench_graph(10000)
        t0 = time.perf_counter()
        plan = derive(g)
        ver = verify(g, plan)
        elapsed = time.perf_counter() - t0
        self.assertTrue(ver.ok)
        self.assertEqual(plan.component_count, 10000)
        self.assertLess(elapsed, 30, f"推导过慢: {elapsed:.1f}s")


if __name__ == "__main__":
    unittest.main(verbosity=2)
