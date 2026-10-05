"""deploy_order 自测：覆盖完全线性、宽并行、成环、可选/弱依赖等情形。"""
import unittest

from deploy_order import (
    DependencyGraph, OPTIONAL, STRONG, WEAK, plan, validate,
)


def chain_graph(n):
    g = DependencyGraph()
    for i in range(1, n):
        g.add_dependency(f"c{i}", f"c{i-1}", STRONG)
    return g


class TestLinear(unittest.TestCase):
    def test_fully_linear(self):
        n = 50
        p = plan(chain_graph(n))
        self.assertEqual(len(p.layers), n)
        self.assertTrue(all(len(layer) == 1 for layer in p.layers))
        for i, layer in enumerate(p.layers):
            self.assertEqual(layer, [f"c{i}"])
        data = validate(chain_graph(n), p)
        self.assertTrue(data["ok"])
        self.assertEqual(data["strong_satisfied"], n - 1)
        self.assertFalse(p.cyclic)


class TestWideParallel(unittest.TestCase):
    def test_wide_parallel(self):
        g = DependencyGraph()
        g.add_component("base")
        k = 8
        for i in range(k):
            g.add_dependency(f"svc{i}", "base", STRONG)
            g.add_dependency("web", f"svc{i}", STRONG)
        p = plan(g)
        self.assertEqual(len(p.layers), 3)
        self.assertEqual(p.layers[0], ["base"])
        self.assertEqual(len(p.layers[1]), k)
        self.assertEqual(p.layers[2], ["web"])
        data = validate(g, p)
        self.assertTrue(data["ok"])
        self.assertEqual(data["max_layer_width"], k)


class TestCycle(unittest.TestCase):
    def cyclic_graph(self):
        g = DependencyGraph()
        # auth <-> user <-> cache 三环，外加正常依赖
        g.add_dependency("auth", "user", STRONG)
        g.add_dependency("user", "cache", STRONG)
        g.add_dependency("cache", "auth", STRONG)
        g.add_dependency("gateway", "auth", STRONG)
        g.add_dependency("db", "base", STRONG)
        g.add_dependency("cache", "db", STRONG)
        return g

    def test_cycle_broken_and_valid(self):
        g = self.cyclic_graph()
        p = plan(g)
        self.assertTrue(p.cyclic)
        self.assertGreaterEqual(len(p.cut_edges), 1)
        self.assertEqual(len(p.breaks), 1)
        brk = p.breaks[0]
        self.assertEqual(brk.components, ["auth", "cache", "user"])
        self.assertTrue(set(brk.affected) <= set(brk.components))
        # 断边后的每步内部无先后约束，步数 >= 2 才称得上"分步"
        self.assertGreaterEqual(len(brk.steps), 2)
        data = validate(g, p)
        self.assertTrue(data["ok"])
        self.assertEqual(data["strong_cut"], len(p.cut_edges))
        self.assertEqual(data["strong_violations"], [])

    def test_self_loop(self):
        g = DependencyGraph()
        g.add_dependency("a", "a", STRONG)
        g.add_dependency("b", "a", STRONG)
        p = plan(g)
        self.assertIn(("a", "a"), p.cut_edges)
        self.assertTrue(validate(g, p)["ok"])


class TestWeakAndOptional(unittest.TestCase):
    def test_optional_does_not_change_layers(self):
        g1 = DependencyGraph()
        g1.add_component("metrics")
        g1.add_dependency("app", "lib", STRONG)
        g1.add_dependency("lib", "os", STRONG)
        p1 = plan(g1)
        g2 = DependencyGraph()
        g2.add_dependency("app", "lib", STRONG)
        g2.add_dependency("lib", "os", STRONG)
        g2.add_dependency("app", "metrics", OPTIONAL)   # 可选，反向
        g2.add_dependency("os", "app", OPTIONAL)        # 可选，成环也不影响
        p2 = plan(g2)
        self.assertEqual(p1.layers, p2.layers)
        self.assertEqual(len(p2.optional), 2)
        self.assertFalse(p2.cyclic)
        self.assertTrue(validate(g2, p2)["ok"])

    def test_weak_honored_when_consistent(self):
        g = DependencyGraph()
        g.add_dependency("app", "lib", STRONG)
        g.add_dependency("app", "config", WEAK)  # 与强依赖方向一致
        p = plan(g)
        self.assertIn(("app", "config"), p.weak_honored)
        self.assertEqual(p.weak_deferred, [])
        self.assertTrue(validate(g, p)["ok"])

    def test_weak_deferred_when_conflicting(self):
        g = DependencyGraph()
        g.add_dependency("a", "b", STRONG)
        g.add_dependency("b", "a", WEAK)  # 与强依赖冲突，必须放弃
        p = plan(g)
        self.assertIn(("b", "a"), p.weak_deferred)
        self.assertFalse(p.cyclic)
        self.assertTrue(validate(g, p)["ok"])

    def test_kind_priority(self):
        g = DependencyGraph()
        g.add_dependency("a", "b", OPTIONAL)
        g.add_dependency("a", "b", WEAK)
        g.add_dependency("a", "b", STRONG)
        p = plan(g)
        self.assertEqual(p.optional, [])
        self.assertEqual(p.weak_honored, [])
        self.assertEqual(p.weak_deferred, [])
        self.assertEqual(validate(g, p)["strong_total"], 1)


class TestValidator(unittest.TestCase):
    def test_validator_catches_tampered_plan(self):
        g = chain_graph(5)
        p = plan(g)
        # 人为破坏：把 c4 挪到第 0 层
        p.layers[4].remove("c4")
        p.layers[0].append("c4")
        p.layer_of["c4"] = 0
        data = validate(g, p)
        self.assertFalse(data["ok"])
        self.assertIn(("c4", "c3"), data["strong_violations"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
