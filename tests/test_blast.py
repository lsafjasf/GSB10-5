"""自测：无影响 / 单点 / 级联 / 冗余生效 / 冗余同时失效 / 偏差对比 / 规模。

运行：python -m unittest discover -s tests -v
"""
import unittest

from blast.assessment import assess
from blast.compare import compare_once
from blast.simulate import simulate_drill
from blast.topology import (
    DependencyGroup,
    Service,
    Topology,
    SCENARIOS,
    generate_large_topology,
)


class ScenarioTests(unittest.TestCase):
    def result(self, name):
        topo, injected = SCENARIOS[name]()
        return topo, injected, assess(topo, injected)

    def test_no_impact(self):
        _, _, r = self.result("no_impact")
        self.assertEqual(r.faulted, set())
        self.assertEqual(r.down, set())
        self.assertEqual(r.impacted, set())
        self.assertEqual(r.affected_user_fraction, 0.0)
        self.assertEqual(r.critical_path_count, 0)
        self.assertEqual(r.defeated_redundancy, [])

    def test_single_point(self):
        _, _, r = self.result("single_point")
        self.assertEqual(r.faulted, {"svc_a"})
        self.assertEqual(r.down, {"svc_a"})
        self.assertEqual(r.impacted, {"svc_a", "gateway"})
        self.assertAlmostEqual(r.affected_user_fraction, 0.7, places=6)
        self.assertEqual(r.critical_path_count, 1)
        self.assertEqual(r.single_point_failures, ["gateway->g0"])

    def test_cascade(self):
        _, _, r = self.result("cascade")
        self.assertEqual(r.faulted, {"svc_c"})
        self.assertEqual(r.down, {"svc_c", "svc_b", "svc_a", "gateway"})
        self.assertEqual(len(r.impacted), 5)
        self.assertAlmostEqual(r.affected_user_fraction, 0.8, places=6)
        self.assertEqual(r.critical_path_count, 2)
        knock_chain = [r.knock_group[s] for s in
                       ("svc_b", "svc_a", "gateway")]
        self.assertEqual(knock_chain, ["svc_b->g0", "svc_a->g0", "gateway->g0"])

    def test_redundancy_holds(self):
        _, _, r = self.result("redundancy_holds")
        self.assertEqual(r.down, {"svc_primary"})
        self.assertEqual(r.affected_user_fraction, 0.0)
        self.assertEqual(len(r.protected_redundancy), 1)
        self.assertEqual(r.defeated_redundancy, [])
        self.assertEqual(r.critical_path_count, 0)

    def test_redundancy_defeated_shared_domain(self):
        _, _, r = self.result("redundancy_defeated")
        self.assertEqual(len(r.defeated_redundancy), 1)
        info = r.defeated_redundancy[0]
        self.assertEqual(info["down_providers"],
                         ["svc_primary", "svc_replica"])
        self.assertIn("az-1", info["cause"])
        self.assertEqual(r.down,
                         {"svc_primary", "svc_replica", "backend", "gateway"})
        self.assertEqual(r.affected_user_fraction, 1.0)
        self.assertEqual(r.critical_path_count, 2)

    def test_mixed_failure_types(self):
        _, _, r = self.result("mixed")
        self.assertEqual(r.faulted, {"a1", "b"})
        self.assertEqual(r.down, {"a1", "b", "gw2"})
        self.assertEqual(r.impacted, {"a1", "b", "gw1", "gw2"})
        self.assertAlmostEqual(r.affected_user_fraction, 0.37, places=6)
        self.assertEqual(len(r.protected_redundancy), 1)

    def test_quorum_group(self):
        """三副本需要 2 个健康：挂 1 个降级，挂 2 个组失效。"""
        def build():
            topo = Topology([Service("gw", entry=True, entry_weight=1.0),
                             Service("r1", fail_domain="d1"),
                             Service("r2", fail_domain="d2"),
                             Service("r3", fail_domain="d3")])
            topo.add_group("gw", ["r1", "r2", "r3"], min_healthy=2)
            return topo
        r1 = assess(build(), {"d1"})
        self.assertAlmostEqual(r1.affected_user_fraction, 0.0)
        self.assertEqual(len(r1.protected_redundancy), 1)
        r2 = assess(build(), {"d1", "d2"})
        self.assertEqual(r2.affected_user_fraction, 1.0)
        self.assertEqual(len(r2.defeated_redundancy), 1)
        self.assertEqual(r2.down, {"r1", "r2", "gw"})

    def test_cyclic_topology(self):
        """带环拓扑：损失按不动点迭代，环上无故障时损失为 0。"""
        topo = Topology([
            Service("gw", entry=True, entry_weight=1.0),
            Service("a", fail_domain="h"),
            Service("b"),
        ])
        topo.add_group("gw", ["a"])
        topo.add_group("a", ["b"])
        topo.add_group("b", ["a"])
        r = assess(topo, {"h"})
        self.assertEqual(r.down, {"a", "b", "gw"})
        self.assertAlmostEqual(r.affected_user_fraction, 1.0)


class ModelValidationTests(unittest.TestCase):
    def test_invalid_entry_weights(self):
        with self.assertRaises(ValueError):
            Topology([Service("gw", entry=True, entry_weight=0.5)])

    def test_missing_provider(self):
        topo = Topology([Service("gw", entry=True, entry_weight=1.0)])
        with self.assertRaises(ValueError):
            topo.add_group("gw", ["ghost"])

    def test_group_validation(self):
        with self.assertRaises(ValueError):
            DependencyGroup("g", ("a",), min_healthy=2)


class ComparisonTests(unittest.TestCase):
    def test_no_impact_matches_drill(self):
        topo, injected = SCENARIOS["no_impact"]()
        dev = compare_once(topo, injected)["deviation"]
        self.assertEqual(dev["absolute_error"], 0.0)
        self.assertEqual(dev["down_set_jaccard"], 1.0)

    def test_estimation_within_sampling_error(self):
        for name in ("single_point", "cascade", "redundancy_holds",
                     "redundancy_defeated"):
            with self.subTest(name=name):
                topo, injected = SCENARIOS[name]()
                dev = compare_once(topo, injected)["deviation"]
                self.assertLess(dev["absolute_error"], 0.02,
                                f"{name} 偏差超抽样噪声范围")
                self.assertGreaterEqual(dev["down_set_jaccard"], 0.9)

    def test_failover_failure_is_a_deviation_source(self):
        topo, injected = SCENARIOS["mixed"]()
        dev = compare_once(topo, injected)["deviation"]
        self.assertGreater(dev["absolute_error"], 0.05)
        self.assertLess(dev["affected_user_fraction_estimate"],
                        dev["affected_user_fraction_observed"])

    def test_simulation_deterministic_with_seed(self):
        topo, injected = SCENARIOS["single_point"]()
        a = simulate_drill(topo, injected, seed=1).failed_requests
        b = simulate_drill(topo, injected, seed=1).failed_requests
        self.assertEqual(a, b)


class ScaleTests(unittest.TestCase):
    def test_large_topology_scale(self):
        topo = generate_large_topology(n_services=10_000, n_edges=100_000)
        self.assertEqual(len(topo.services), 10_000)
        self.assertGreaterEqual(topo.provider_count(), 90_000)
        domain = next(iter(topo.services.values())).fail_domain
        r = assess(topo, {domain})
        self.assertGreaterEqual(len(r.down), 1)


if __name__ == "__main__":
    unittest.main()
