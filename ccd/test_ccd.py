"""test_ccd.py — CCD 库自测。运行：python3 test_ccd.py -v"""

import math
import unittest

from ccd import (COLLIDE, OVERLAP, TOUCH, Box, Circle, simulate,
                 sweep_circle_box, sweep_circle_circle)


def overlap_at(a, b, t, dt):
    """朴素离散检测：只看 t*dt 时刻是否重叠（用于演示穿透漏检）。"""
    ax, ay = a.x + a.vx * t * dt, a.y + a.vy * t * dt
    bx, by = b.x + b.vx * t * dt, b.y + b.vy * t * dt
    return (ax - bx) ** 2 + (ay - by) ** 2 <= (a.r + b.r) ** 2


class TestStatic(unittest.TestCase):
    def test_both_static_separated(self):
        a = Circle(0, 0, 0, 0, 1)
        b = Circle(5, 0, 0, 0, 1)
        self.assertIsNone(sweep_circle_circle(a, b))

    def test_both_static_overlapping(self):
        a = Circle(0, 0, 0, 0, 1)
        b = Circle(1.5, 0, 0, 0, 1)
        t, kind = sweep_circle_circle(a, b)
        self.assertEqual(t, 0.0)
        self.assertEqual(kind, OVERLAP)

    def test_moving_away(self):
        a = Circle(0, 0, -1, 0, 1)
        b = Circle(5, 0, 0, 0, 1)
        self.assertIsNone(sweep_circle_circle(a, b))


class TestSlowContact(unittest.TestCase):
    def test_slow_contact_toi(self):
        # 相距 5，R=2，相对速度 1 → 3 秒接触；dt=10 → t=0.3
        a = Circle(0, 0, 1, 0, 1)
        b = Circle(5, 0, 0, 0, 1)
        t, kind = sweep_circle_circle(a, b, dt=10.0)
        self.assertEqual(kind, COLLIDE)
        self.assertAlmostEqual(t, 0.3, places=12)

    def test_contact_after_frame(self):
        a = Circle(0, 0, 1, 0, 1)
        b = Circle(5, 0, 0, 0, 1)
        self.assertIsNone(sweep_circle_circle(a, b, dt=2.0))  # 2s 内够不着


class TestHighSpeedTunneling(unittest.TestCase):
    def test_tunneling_caught(self):
        # 每帧位移 1000，自身长度 1：离散检测必然穿透
        a = Circle(0, 0, 1000, 0, 0.5)
        b = Circle(100, 0, 0, 0, 0.5)
        self.assertFalse(overlap_at(a, b, 1.0, 1.0))  # 帧末离散检测：漏检
        t, kind = sweep_circle_circle(a, b, dt=1.0)
        self.assertEqual(kind, COLLIDE)
        self.assertAlmostEqual(t, 0.099, places=12)  # (100-1)/1000

    def test_fast_but_missing(self):
        a = Circle(0, 0, 1000, 0, 0.5)
        b = Circle(100, 5, 0, 0, 0.5)  # 横向偏移 5 > R=1
        self.assertIsNone(sweep_circle_circle(a, b))


class TestTangent(unittest.TestCase):
    def test_exact_tangent_is_touch_not_collide(self):
        # 最近距离恰好 = R：擦边。必须报告 TOUCH，且不得是 COLLIDE
        a = Circle(0, 0, 1, 0, 1)
        b = Circle(5, 2, 0, 0, 1)
        t, kind = sweep_circle_circle(a, b, dt=10.0)
        self.assertEqual(kind, TOUCH)
        self.assertAlmostEqual(t, 0.5, places=12)

    def test_just_above_tangent_misses(self):
        a = Circle(0, 0, 1, 0, 1)
        b = Circle(5, 2 + 1e-7, 0, 0, 1)
        self.assertIsNone(sweep_circle_circle(a, b, dt=10.0))

    def test_just_below_tangent_collides(self):
        a = Circle(0, 0, 1, 0, 1)
        b = Circle(5, 2 - 1e-7, 0, 0, 1)
        t, kind = sweep_circle_circle(a, b, dt=10.0)
        self.assertEqual(kind, COLLIDE)
        self.assertAlmostEqual(t, 0.5, delta=1e-3)

    def test_tangent_does_not_hang_simulate(self):
        # 擦边后两物体分离：simulate 应只报告一次 TOUCH 并正常结束
        a = Circle(0, 0, 1, 0, 1)
        b = Circle(5, 2, 0, 0, 1)
        events, bodies = simulate([a, b], dt=10.0)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][1], TOUCH)
        self.assertAlmostEqual(events[0][0], 5.0, places=9)
        self.assertEqual(bodies[0].vx, 1.0)  # 擦边不改变速度


class TestCircleBox(unittest.TestCase):
    def test_face_hit(self):
        c = Circle(0, 0, 10, 0, 1)
        wall = Box(10, 0, 0.5, 10)
        t, kind = sweep_circle_box(c, wall, dt=1.0)
        self.assertEqual(kind, COLLIDE)
        self.assertAlmostEqual(t, 0.85, places=12)  # (10-0.5-1)/10

    def test_corner_hit(self):
        # 斜向打角：盒心 (3,3) 半宽 2，角在 (1,1)；圆沿 (1,1) 运动
        c = Circle(0, 0, 1, 1, 1)
        box = Box(3, 3, 2, 2)
        t, kind = sweep_circle_box(c, box, dt=1.0)
        self.assertEqual(kind, COLLIDE)
        self.assertAlmostEqual(t, 1.0 - 1.0 / math.sqrt(2), places=12)

    def test_miss(self):
        c = Circle(0, 5, 10, 0, 1)
        wall = Box(10, 0, 0.5, 1)
        self.assertIsNone(sweep_circle_box(c, wall))


class TestMultiCollisionOrdering(unittest.TestCase):
    def test_newtons_cradle_sequence(self):
        # A 撞 B（t=1），B 再撞 C（t=1.5），事件必须按时间排序
        a = Circle(0, 0, 1, 0, 1)
        b = Circle(3, 0, 0, 0, 1)
        c = Circle(5.5, 0, 0, 0, 1)
        events, bodies = simulate([a, b, c], dt=2.0)
        self.assertEqual([(e[1], e[2], e[3]) for e in events],
                         [(COLLIDE, 0, 1), (COLLIDE, 1, 2)])
        self.assertAlmostEqual(events[0][0], 1.0, places=9)
        self.assertAlmostEqual(events[1][0], 1.5, places=9)
        # 等质量弹性：动量沿链传递，最终 C 以 1 运动，A、B 静止
        self.assertAlmostEqual(bodies[0].vx, 0.0, places=9)
        self.assertAlmostEqual(bodies[1].vx, 0.0, places=9)
        self.assertAlmostEqual(bodies[2].vx, 1.0, places=9)

    def test_wall_bounces_ordered(self):
        # 球在两墙间往返：一帧内多次碰撞，时刻严格递增
        ball = Circle(0, 0, 10, 0, 1)
        left = Box(-10, 0, 0.5, 10)
        right = Box(10, 0, 0.5, 10)
        events, bodies = simulate([ball, left, right], dt=3.0)
        times = [e[0] for e in events]
        # 0.85 撞右墙；反弹后行程 17（8.5 -> -8.5）→ 1.7s → 2.55 撞左墙
        self.assertEqual(len(events), 2)
        self.assertEqual(times, sorted(times))
        self.assertAlmostEqual(times[0], 0.85, places=9)
        self.assertAlmostEqual(times[1], 2.55, places=9)
        self.assertAlmostEqual(bodies[0].vx, 10.0, places=9)  # 两次反弹后向右


if __name__ == "__main__":
    unittest.main()
