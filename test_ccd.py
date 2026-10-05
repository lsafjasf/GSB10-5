"""CCD 自测：静止 / 慢速 / 高速穿透 / 恰好相切 / 一帧多碰撞 / 对拍误差。

运行：python3 test_ccd.py -v
"""

import math
import random
import unittest

from ccd import (HIT, TANGENT, OVERLAP, World,
                 sweep_circle_circle, sweep_circle_segment)


def reference_toi(px, py, vx, vy, cx, cy, radius, dt, samples=4096):
    """极小步长扫描 + 二分加密的参考解（与库实现相互独立）。"""
    def f(t):
        dx = px + vx * t - cx
        dy = py + vy * t - cy
        return dx * dx + dy * dy - radius * radius

    if f(0.0) <= 0.0:
        return 0.0
    prev = 0.0
    for i in range(1, samples + 1):
        t = dt * i / samples
        if f(t) <= 0.0:
            lo, hi = prev, t
            for _ in range(200):
                mid = 0.5 * (lo + hi)
                if f(mid) <= 0.0:
                    hi = mid
                else:
                    lo = mid
            return 0.5 * (lo + hi)
        prev = t
    return None


class TestSweepCircleCircle(unittest.TestCase):
    def test_static_slow_contact(self):
        # 静止目标、慢速接近：解析解 t = (dist - r1 - r2) / |v| = (5-1)/1 = 4
        res = sweep_circle_circle(0, 0, 1, 0, 0.5, 5, 0, 0.5, 10.0)
        self.assertIsNotNone(res)
        t, kind = res
        self.assertEqual(kind, HIT)
        self.assertAlmostEqual(t, 4.0, places=12)

    def test_high_speed_tunneling(self):
        # 一帧位移 1000，帧末位置 (1000,0) 已越过目标：离散检测必漏，CCD 命中
        res = sweep_circle_circle(0, 0, 1000, 0, 0.5, 500, 0, 0.5, 1.0)
        self.assertIsNotNone(res)
        t, kind = res
        self.assertEqual(kind, HIT)
        self.assertAlmostEqual(t, (500.0 - 1.0) / 1000.0, places=12)

    def test_moving_target_relative_velocity(self):
        # 目标同向慢速运动：相对速度 6，解析解 t = (10-2)/6
        w = World()
        b = w.add_body(0, 0, 8, 0, 1, "chaser")
        w.add_body(10, 0, 2, 0, 1, "target")
        events = w.step(2.0)
        hits = [e for e in events if e.kind == "collision"]
        self.assertEqual(len(hits), 1)
        self.assertAlmostEqual(hits[0].t, 8.0 / 6.0, places=12)

    def test_no_motion_no_event(self):
        self.assertIsNone(sweep_circle_circle(0, 0, 0, 0, 0.5, 5, 0, 0.5, 1.0))

    def test_initial_overlap(self):
        res = sweep_circle_circle(0, 0, 1, 0, 1.0, 1.5, 0, 1.0, 1.0)
        self.assertEqual(res, (0.0, OVERLAP))

    def test_frame_end_miss_means_no_event(self):
        # 接触时刻在帧末之后 -> 本帧无事件
        self.assertIsNone(sweep_circle_circle(0, 0, 1, 0, 0.5, 5, 0, 0.5, 1.0))


class TestTangency(unittest.TestCase):
    """恰好相切：必须检出（tangent），但不得判为碰撞（hit）。"""

    def test_exact_tangent_is_reported_not_collision(self):
        # 最近距离恰好 = r1+r2 = 1.0
        res = sweep_circle_circle(0, 0, 1, 0, 0.5, 5, 1.0, 0.5, 10.0)
        self.assertIsNotNone(res)
        t, kind = res
        self.assertEqual(kind, TANGENT)
        self.assertAlmostEqual(t, 5.0, places=12)

    def test_just_outside_tangent_no_event(self):
        # 外偏 1e-6：互不相碰，无任何事件
        res = sweep_circle_circle(0, 0, 1, 0, 0.5, 5, 1.0 + 1e-6, 0.5, 10.0)
        self.assertIsNone(res)

    def test_just_inside_tangent_is_collision(self):
        # 内偏 1e-6：真实碰撞
        res = sweep_circle_circle(0, 0, 1, 0, 0.5, 5, 1.0 - 1e-6, 0.5, 10.0)
        self.assertIsNotNone(res)
        t, kind = res
        self.assertEqual(kind, HIT)
        self.assertLess(t, 5.0)

    def test_tangent_does_not_change_motion_in_world(self):
        w = World()
        b = w.add_body(0, 0, 1, 0, 0.5, "slider")
        w.add_circle(5, 1.0, 0.5, "post")
        events = w.step(10.0)
        kinds = [e.kind for e in events]
        self.assertEqual(kinds, ["tangent"])
        # 相切不反弹：终点 = 匀速运动终点
        self.assertAlmostEqual(b.x, 10.0, places=12)
        self.assertAlmostEqual(b.y, 0.0, places=12)
        self.assertEqual((b.vx, b.vy), (1, 0))

    def test_sliding_along_wall_exactly_tangent(self):
        # 与墙面恰好相距 r 平行滑动 -> tangent，且不是 collision
        res = sweep_circle_segment(0, 0.5, 1, 0, 0.5, -1, 0, 11, 0, 10.0)
        self.assertIsNotNone(res)
        self.assertEqual(res[1], TANGENT)


class TestSegment(unittest.TestCase):
    def test_side_hit(self):
        res = sweep_circle_segment(5, 5, 0, -1, 0.5, 0, 0, 10, 0, 10.0)
        self.assertIsNotNone(res)
        t, kind, nx, ny = res
        self.assertEqual(kind, HIT)
        self.assertAlmostEqual(t, 4.5, places=12)
        self.assertAlmostEqual(ny, 1.0, places=12)

    def test_endpoint_hit(self):
        res = sweep_circle_segment(0, 5, 0, -1, 0.5, 0, 0, 10, 0, 10.0)
        self.assertIsNotNone(res)
        t, kind, nx, ny = res
        self.assertEqual(kind, HIT)
        self.assertAlmostEqual(t, 4.5, places=12)

    def test_high_speed_through_wall(self):
        # 高速穿墙：v=1000 一帧越过线段，CCD 仍命中
        res = sweep_circle_segment(5, 500, 0, -1000, 0.5, 0, 0, 10, 0, 1.0)
        self.assertIsNotNone(res)
        self.assertAlmostEqual(res[0], (500.0 - 0.5) / 1000.0, places=12)


class TestReferenceComparison(unittest.TestCase):
    """与极小步长 + 二分参考解对拍，输出误差数据。"""

    def test_toi_vs_reference_random(self):
        rng = random.Random(20261005)
        errors = []
        n_cases = 0
        for _ in range(500):
            px, py = rng.uniform(-10, 10), rng.uniform(-10, 10)
            cx, cy = rng.uniform(-10, 10), rng.uniform(-10, 10)
            # 速度朝向目标并加随机扰动，提高命中样本量
            base = math.atan2(cy - py, cx - px)
            ang = base + rng.uniform(-0.8, 0.8)
            speed = rng.uniform(0.1, 50.0)
            vx, vy = speed * math.cos(ang), speed * math.sin(ang)
            r1 = rng.uniform(0.1, 2.0)
            r2 = rng.uniform(0.1, 2.0)
            dt = rng.uniform(0.1, 2.0)
            res = sweep_circle_circle(px, py, vx, vy, r1, cx, cy, r2, dt)
            ref = reference_toi(px, py, vx, vy, cx, cy, r1 + r2, dt)
            if res is None or res[1] != HIT:
                continue  # 相切/未命中不参与 TOI 对拍
            self.assertIsNotNone(ref, "CCD 命中但参考解未命中")
            errors.append(abs(res[0] - ref))
            n_cases += 1
        max_err = max(errors)
        mean_err = sum(errors) / len(errors)
        print(f"\n[对拍] 圆-圆随机 {n_cases} 例命中："
              f"TOI 最大误差 {max_err:.3e}，平均误差 {mean_err:.3e}"
              f"（参考解：4096 步扫描 + 200 次二分）")
        self.assertLess(max_err, 1e-6)

    def test_toi_vs_analytic(self):
        # 构造有精确解析解的情形：正对圆心匀速接近
        cases = [
            (0, 0, 2, 0, 1.0, 10, 0, 1.0, (10 - 2) / 2),
            (0, 0, 0, 5, 0.5, 0, 20, 0.5, (20 - 1) / 5),
            (0, 0, 3, 4, 1.0, 30, 40, 2.0, (50 - 3) / 5),  # 3-4-5 方向
        ]
        worst = 0.0
        for px, py, vx, vy, r1, cx, cy, r2, t_exact in cases:
            res = sweep_circle_circle(px, py, vx, vy, r1, cx, cy, r2, 1e9)
            self.assertIsNotNone(res)
            err = abs(res[0] - t_exact)
            worst = max(worst, err)
            self.assertLess(err, 1e-9)
        print(f"\n[对拍] 解析解 3 例：TOI 最大误差 {worst:.3e}")


class TestWorldEventSequence(unittest.TestCase):
    def test_multiple_collisions_sorted_in_one_frame(self):
        # 球在四面墙围成的盒子里高速运动，一帧内多次反弹
        w = World()
        w.add_body(1.0, 5.0, 8.0, 3.0, 0.5, "ball")
        w.add_segment(0, 0, 10, 0, "floor")
        w.add_segment(10, 0, 10, 10, "right")
        w.add_segment(10, 10, 0, 10, "ceil")
        w.add_segment(0, 10, 0, 0, "left")
        events = w.step(2.0)
        collisions = [e for e in events if e.kind == "collision"]
        self.assertGreaterEqual(len(collisions), 2)
        times = [e.t for e in events]
        self.assertEqual(times, sorted(times), "事件必须按时间排序")
        print("\n[事件序列] 一帧内多次碰撞（盒子内反弹）：")
        for e in events:
            print("   ", e)
        # 球必须始终留在盒内（含半径）
        self.assertTrue(0.5 - 1e-9 <= w.bodies[0].x <= 9.5 + 1e-9)
        self.assertTrue(0.5 - 1e-9 <= w.bodies[0].y <= 9.5 + 1e-9)

    def test_event_times_match_analytic_1d(self):
        # 两竖墙之间水平弹跳：解析事件时刻 7/6, 15/6
        w = World()
        w.add_body(2.0, 5.0, 6.0, 0.0, 1.0, "ball")
        w.add_segment(0, 0, 0, 10, "left")
        w.add_segment(10, 0, 10, 10, "right")
        events = w.step(3.0)
        times = [e.t for e in events if e.kind == "collision"]
        self.assertEqual(len(times), 2)
        self.assertAlmostEqual(times[0], 7.0 / 6.0, places=12)
        self.assertAlmostEqual(times[1], 15.0 / 6.0, places=12)
        print("\n[事件序列] 两墙间弹跳（解析时刻 7/6, 15/6）：")
        for e in events:
            print("   ", e)

    def test_body_body_collision_sequence(self):
        w = World()
        w.add_body(0, 0, 4, 0, 1, "A")
        w.add_body(6, 0, 0, 0, 1, "B")
        w.add_body(12, 0, 0, 0, 1, "C")
        events = w.step(2.0)
        seq = [(round(e.t, 6), e.body, e.other) for e in events
               if e.kind == "collision"]
        # A 先撞 B（t=1.0），B 再撞 C（t=1.0+4/4=2.0 前）
        self.assertEqual(seq[0][1:], ("A", "B"))
        self.assertAlmostEqual(seq[0][0], 1.0, places=9)
        self.assertEqual(times_sorted(events), True)
        print("\n[事件序列] 三球连锁碰撞：")
        for e in events:
            print("   ", e)


def times_sorted(events):
    ts = [e.t for e in events]
    return ts == sorted(ts)


if __name__ == "__main__":
    unittest.main(verbosity=2)
