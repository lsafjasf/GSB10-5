"""自测：python3 -m unittest discover -s tests -v"""

import itertools
import random
import unittest

from release_conflict import Release, Resource, detect_conflicts, schedule


def brute_force_conflicts(releases, resources):
    """暴力法：枚举所有发布单子集，找出全部最小冲突集（用于交叉校验）。"""
    resources = dict(resources)
    out = set()
    rels = list(releases)
    for size in range(1, len(rels) + 1):
        for combo in itertools.combinations(rels, size):
            common = set(combo[0].resource_demands)
            for r in combo[1:]:
                common &= set(r.resource_demands)
            for rid in common:
                cap = resources[rid].capacity
                lo = max(r.start for r in combo)
                hi = min(r.end for r in combo)
                if lo >= hi:
                    continue
                demand = sum(r.resource_demands[rid] for r in combo)
                if demand <= cap:
                    continue
                minimal = True
                for k in range(1, size):
                    for sub in itertools.combinations(combo, k):
                        slo = max(r.start for r in sub)
                        shi = min(r.end for r in sub)
                        if slo >= shi:
                            continue
                        if sum(r.resource_demands[rid] for r in sub) > cap:
                            minimal = False
                            break
                    if not minimal:
                        break
                if minimal:
                    out.add((rid, tuple(sorted(r.id for r in combo))))
    return out


def conflict_keys(conflicts):
    return {(c.resource, c.releases) for c in conflicts}


class TestConflictRules(unittest.TestCase):
    def setUp(self):
        self.exclusive = {"r1": Resource("r1", 1), "r2": Resource("r2", 1)}

    def test_time_overlap_disjoint_resources_no_conflict(self):
        """时间重叠但资源不相干 -> 不冲突"""
        rels = [
            Release("a", 0, 10, {"r1": 1}),
            Release("b", 0, 10, {"r2": 1}),
        ]
        self.assertEqual(detect_conflicts(rels, self.exclusive), [])

    def test_same_resource_disjoint_time_no_conflict(self):
        """资源相同但占用时段不相交 -> 不冲突"""
        rels = [
            Release("a", 0, 10, {"r1": 1}),
            Release("b", 20, 30, {"r1": 1}),
        ]
        self.assertEqual(detect_conflicts(rels, self.exclusive), [])

    def test_touching_boundaries_no_conflict(self):
        """端点相接（半开区间）不算重叠 -> 不冲突"""
        rels = [
            Release("a", 0, 10, {"r1": 1}),
            Release("b", 10, 20, {"r1": 1}),
        ]
        self.assertEqual(detect_conflicts(rels, self.exclusive), [])

    def test_completely_unrelated_no_conflict(self):
        """完全不相干 -> 不冲突"""
        rels = [
            Release("a", 0, 5, {"r1": 1}),
            Release("b", 100, 200, {"r2": 1}),
        ]
        self.assertEqual(detect_conflicts(rels, self.exclusive), [])

    def test_pairwise_conflict_locates_resource_and_releases(self):
        """同资源 + 时间重叠 -> 冲突，且定位到资源、两个发布单与重叠区间"""
        rels = [
            Release("a", 0, 10, {"r1": 1}),
            Release("b", 5, 15, {"r1": 1}),
        ]
        (c,) = detect_conflicts(rels, self.exclusive)
        self.assertEqual(c.resource, "r1")
        self.assertEqual(c.releases, ("a", "b"))
        self.assertEqual(c.overlap, (5, 10))
        self.assertEqual((c.demand, c.capacity), (2, 1))

    def test_transitive_triple_conflict(self):
        """甲乙、乙丙、甲丙两两不冲突，但三者一起超载 -> 三元最小冲突集"""
        resources = {"pool": Resource("pool", 2)}
        rels = [
            Release("x", 0, 10, {"pool": 1}),
            Release("y", 3, 13, {"pool": 1}),
            Release("z", 6, 8, {"pool": 1}),
        ]
        conflicts = detect_conflicts(rels, resources)
        self.assertEqual(len(conflicts), 1)
        c = conflicts[0]
        self.assertEqual(c.resource, "pool")
        self.assertEqual(c.releases, ("x", "y", "z"))
        self.assertEqual(c.overlap, (6, 8))
        self.assertEqual((c.demand, c.capacity), (3, 2))
        for drop in ("x", "y", "z"):
            rest = [r for r in rels if r.id != drop]
            self.assertEqual(detect_conflicts(rest, resources), [])

    def test_weighted_demand_conflict(self):
        """占用量非 1 的情形：2+2 > 3"""
        resources = {"pool": Resource("pool", 3)}
        rels = [
            Release("a", 0, 10, {"pool": 2}),
            Release("b", 5, 9, {"pool": 2}),
        ]
        (c,) = detect_conflicts(rels, resources)
        self.assertEqual(c.releases, ("a", "b"))
        self.assertEqual((c.demand, c.capacity), (4, 3))

    def test_no_conflict_when_capacity_sufficient(self):
        """容量足够 -> 不冲突"""
        resources = {"pool": Resource("pool", 2)}
        rels = [
            Release("a", 0, 10, {"pool": 1}),
            Release("b", 5, 9, {"pool": 1}),
        ]
        self.assertEqual(detect_conflicts(rels, resources), [])

    def test_oversized_single_release_reported(self):
        """单个发布单占用量就超容量 -> 直接报告"""
        resources = {"pool": Resource("pool", 1)}
        rels = [Release("a", 0, 10, {"pool": 2})]
        (c,) = detect_conflicts(rels, resources)
        self.assertEqual(c.releases, ("a",))

    def test_default_resources_are_exclusive(self):
        """不传资源表时，资源一律按独占处理"""
        rels = [
            Release("a", 0, 10, {"r1": 1}),
            Release("b", 5, 15, {"r1": 1}),
        ]
        self.assertEqual(len(detect_conflicts(rels)), 1)

    def test_invalid_release_rejected(self):
        with self.assertRaises(ValueError):
            Release("bad", 10, 10, {"r1": 1})
        with self.assertRaises(ValueError):
            Release("bad", 0, 10, {})


class TestScheduler(unittest.TestCase):
    def test_earliest_start_respects_conflicts(self):
        resources = {"r1": Resource("r1", 1)}
        rels = [
            Release("a", 0, 10, {"r1": 1}),
            Release("b", 5, 15, {"r1": 1}),
        ]
        plan = {s.release.id: s for s in schedule(rels, resources)}
        self.assertEqual((plan["a"].start, plan["a"].end), (0, 10))
        self.assertEqual((plan["b"].start, plan["b"].end), (10, 20))

    def test_never_earlier_than_requested(self):
        resources = {"r1": Resource("r1", 1)}
        rels = [Release("a", 100, 110, {"r1": 1})]
        (s,) = schedule(rels, resources)
        self.assertEqual(s.start, 100)

    def test_scheduled_plan_is_conflict_free(self):
        """随机场景：排期结果重新检测必须无冲突，且不早于期望时间"""
        rng = random.Random(7)
        resources = {
            "r1": Resource("r1", 1),
            "r2": Resource("r2", 1),
            "pool": Resource("pool", 2),
        }
        rels = []
        for i in range(60):
            start = rng.randrange(0, 200)
            dur = rng.randrange(5, 30)
            rid = rng.choice(list(resources))
            rels.append(Release(f"r{i}", start, start + dur, {rid: 1}))
        plan = schedule(rels, resources)
        executed = [
            Release(s.release.id, s.start, s.end, dict(s.release.resource_demands))
            for s in plan
        ]
        self.assertEqual(detect_conflicts(executed, resources), [])
        for s in plan:
            self.assertGreaterEqual(s.start, s.release.start)
            self.assertEqual(s.end - s.start, s.release.duration)


class TestAgainstBruteForce(unittest.TestCase):
    def test_random_small_cases_match_brute_force(self):
        """小容量随机场景与暴力枚举的最小冲突集完全一致"""
        rng = random.Random(42)
        for trial in range(30):
            resources = {
                "r1": Resource("r1", 1),
                "r2": Resource("r2", 2),
                "r3": Resource("r3", 2),
            }
            rels = []
            for i in range(rng.randrange(2, 8)):
                start = rng.randrange(0, 20)
                dur = rng.randrange(2, 10)
                rid = rng.choice(list(resources))
                demand = rng.choice([1, 1, 1, 2])
                rels.append(Release(f"r{i}", start, start + dur, {rid: demand}))
            got = conflict_keys(detect_conflicts(rels, resources))
            want = brute_force_conflicts(rels, resources)
            self.assertEqual(got, want, f"trial={trial} rels={rels}")


if __name__ == "__main__":
    unittest.main()
