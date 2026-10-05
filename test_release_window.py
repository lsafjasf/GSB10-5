"""release_window 自测：覆盖完全不相干、纯时间重叠、纯资源重叠、
时间+资源双重叠、传递性冲突、排期正确性与 10 万级冒烟。"""

import unittest

from release_window import (
    Release,
    conflict_groups,
    detect_conflicts,
    generate_releases,
    schedule_earliest,
)

H = 60


def R(rid, sh, eh, res):
    return Release(rid, sh * H, eh * H, frozenset(res))


class TestConflictRule(unittest.TestCase):
    def test_fully_unrelated(self):
        # 完全不相干：时间、资源都不沾边
        releases = [R("A", 9, 10, {"db1"}), R("B", 14, 15, {"db2"})]
        self.assertEqual(detect_conflicts(releases), [])

    def test_time_overlap_only(self):
        # 时间重叠但资源不相交 -> 不冲突
        releases = [R("A", 9, 11, {"db1"}), R("B", 10, 12, {"db2"})]
        self.assertEqual(detect_conflicts(releases), [])

    def test_resource_overlap_only(self):
        # 资源相同但时间不重叠（含首尾相接）-> 不冲突
        releases = [R("A", 9, 10, {"db1"}), R("B", 10, 11, {"db1"}),
                    R("C", 20, 21, {"db1"})]
        self.assertEqual(detect_conflicts(releases), [])

    def test_both_overlap_conflicts(self):
        # 时间重叠 + 资源共享 -> 冲突，且定位到具体资源与两个发布单
        releases = [R("A", 9, 11, {"db1", "svc1"}),
                    R("B", 10, 12, {"db1", "svc2"})]
        conflicts = detect_conflicts(releases)
        self.assertEqual(len(conflicts), 1)
        c = conflicts[0]
        self.assertEqual((c.rid_a, c.rid_b), ("A", "B"))
        self.assertEqual(c.shared_resources, ("db1",))

    def test_empty_resources_never_conflict(self):
        releases = [R("A", 9, 11, set()), R("B", 9, 11, set())]
        self.assertEqual(detect_conflicts(releases), [])

    def test_invalid_window_rejected(self):
        with self.assertRaises(ValueError):
            Release("X", 100, 100, frozenset())


class TestTransitiveConflict(unittest.TestCase):
    def test_transitive_chain(self):
        # 甲乙冲突、乙丙冲突、甲丙不直接冲突 -> 三者构成一个传递冲突组
        releases = [
            R("甲", 9, 11, {"db-order"}),
            R("乙", 10, 12, {"db-order", "cache-user"}),
            R("丙", 11, 13, {"cache-user"}),
        ]
        conflicts = detect_conflicts(releases)
        pairs = {frozenset((c.rid_a, c.rid_b)) for c in conflicts}
        self.assertEqual(pairs, {frozenset(("甲", "乙")), frozenset(("乙", "丙"))})
        groups = conflict_groups(releases, conflicts)
        self.assertEqual(groups, [frozenset({"甲", "乙", "丙"})])

    def test_independent_groups(self):
        releases = [
            R("A", 9, 11, {"r1"}), R("B", 10, 12, {"r1"}),
            R("C", 9, 11, {"r2"}), R("D", 10, 12, {"r2"}),
            R("E", 20, 21, {"r3"}),
        ]
        groups = conflict_groups(releases, detect_conflicts(releases))
        self.assertEqual(groups, [frozenset({"A", "B"}), frozenset({"C", "D"})])


class TestScheduler(unittest.TestCase):
    def test_conflicting_releases_serialized(self):
        releases = [R("A", 9, 11, {"db1"}), R("B", 10, 12, {"db1"})]
        plan = schedule_earliest(releases)
        # A 保持原窗口；B 整体顺延到 A 释放 db1 之后
        self.assertEqual(plan["A"], (9 * H, 11 * H))
        self.assertEqual(plan["B"], (11 * H, 13 * H))

    def test_unrelated_releases_untouched(self):
        releases = [R("A", 9, 11, {"db1"}), R("B", 10, 12, {"db2"})]
        plan = schedule_earliest(releases)
        self.assertEqual(plan["A"], (9 * H, 11 * H))
        self.assertEqual(plan["B"], (10 * H, 12 * H))

    def test_no_resource_double_booked(self):
        # 随机数据上验证排期结果：任意资源同一时刻至多一个发布
        releases = generate_releases(5000, seed=7)
        plan = schedule_earliest(releases)
        by_res = {}
        for r in releases:
            s, e = plan[r.rid]
            self.assertEqual(e - s, r.duration)  # 时长不变
            self.assertGreaterEqual(s, r.start)  # 不早于期望时间
            for res in r.resources:
                by_res.setdefault(res, []).append((s, e))
        for res, ivs in by_res.items():
            ivs.sort()
            for (s1, e1), (s2, e2) in zip(ivs, ivs[1:]):
                self.assertLessEqual(e1, s2, f"资源 {res} 被重复占用")


class TestScaleSmoke(unittest.TestCase):
    def test_100k_smoke(self):
        releases = generate_releases(100_000)
        conflicts = detect_conflicts(releases)
        groups = conflict_groups(releases, conflicts)
        plan = schedule_earliest(releases)
        self.assertEqual(len(plan), 100_000)
        self.assertTrue(all(len(g) >= 2 for g in groups))
        for c in conflicts:
            self.assertTrue(c.shared_resources)


if __name__ == "__main__":
    unittest.main(verbosity=2)
