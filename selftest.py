"""自测：覆盖同半球短弧、跨经度分界、极点附近、对跖点、重合点等情形。

运行：python3 selftest.py
"""

import math
import unittest

from slerp import slerp, slerp_path, central_angle, normalize_lon
from reference import ref_slerp, angular_error

TOL_DEG = 1e-5  # 与笛卡尔参考实现的对拍容差（度）


def assert_close_to_ref(testcase, p1, p2, steps=21):
    for i in range(steps):
        t = i / (steps - 1)
        got = slerp(*p1, *p2, t)
        exp = ref_slerp(*p1, *p2, t)
        err = math.degrees(angular_error(got, exp))
        testcase.assertLess(err, TOL_DEG, f"{p1}->{p2} t={t}: err={err}")


class TestSameHemisphere(unittest.TestCase):
    def test_midpoint(self):
        # (0,0)->(0,90) 赤道上，中点应为 (0,45)
        lat, lon = slerp(0.0, 0.0, 0.0, 90.0, 0.5)
        self.assertAlmostEqual(lat, 0.0, places=12)
        self.assertAlmostEqual(lon, 45.0, places=12)

    def test_matches_reference(self):
        assert_close_to_ref(self, (35.0, 120.0), (40.0, 130.0))

    def test_endpoints_exact(self):
        self.assertEqual(slerp(35.0, 120.0, 40.0, 130.0, 0.0), (35.0, 120.0))
        self.assertEqual(slerp(35.0, 120.0, 40.0, 130.0, 1.0), (40.0, 130.0))

    def test_uniform_angular_spacing(self):
        # 相邻插值点的圆心角应相等（沿大圆匀速）
        path = slerp_path(10.0, 20.0, 50.0, 100.0, 8)
        gaps = [central_angle(*path[i], *path[i + 1]) for i in range(8)]
        for gap in gaps:
            self.assertAlmostEqual(gap, gaps[0], places=12)


class TestAntimeridian(unittest.TestCase):
    def test_short_way_across(self):
        # 170 -> -170 必须走 180 分界，中点经度为 ±180，绝不经过 0
        _, lon = slerp(10.0, 170.0, 10.0, -170.0, 0.5)
        self.assertAlmostEqual(abs(lon), 180.0, places=12)

    def test_path_never_wraps_the_long_way(self):
        for lat, lon in slerp_path(10.0, 170.0, -10.0, -170.0, 20):
            self.assertGreater(abs(lon), 90.0)

    def test_central_angle_is_short(self):
        # 与 3D 向量参考值一致，且明显短于绕远路的 340 度
        self.assertAlmostEqual(
            central_angle(10.0, 170.0, 10.0, -170.0),
            angular_error((10.0, 170.0), (10.0, -170.0)), places=14)

    def test_matches_reference(self):
        assert_close_to_ref(self, (10.0, 170.0), (10.0, -170.0))
        assert_close_to_ref(self, (-20.0, 179.9), (30.0, -179.9))


class TestNearPole(unittest.TestCase):
    def test_high_latitude_antimeridian(self):
        # 高纬跨分界应抄极点附近近路，中点纬度 > 80
        lat, _ = slerp(80.0, -170.0, 80.0, 170.0, 0.5)
        self.assertGreater(lat, 80.0)

    def test_over_the_pole(self):
        lat, _ = slerp(89.0, 0.0, 89.0, 180.0, 0.5)
        self.assertAlmostEqual(lat, 90.0, places=9)

    def test_start_at_exact_pole(self):
        # 从北极点出发：沿 p2 经线南下
        lat, lon = slerp(90.0, 0.0, 0.0, 180.0, 0.5)
        self.assertAlmostEqual(lat, 45.0, places=12)
        self.assertAlmostEqual(abs(lon), 180.0, places=12)

    def test_both_poles_same_point(self):
        # (90,0) 与 (90,180) 是同一个点：结果确定，返回 p1
        self.assertEqual(slerp(90.0, 0.0, 90.0, 180.0, 0.3), (90.0, 0.0))

    def test_matches_reference(self):
        assert_close_to_ref(self, (80.0, -170.0), (80.0, 170.0))
        assert_close_to_ref(self, (89.0, 0.0), (89.0, 180.0))
        assert_close_to_ref(self, (90.0, 0.0), (0.0, 180.0))


class TestAntipodal(unittest.TestCase):
    def test_deterministic_meridian_path(self):
        # 约定：lat1 >= 0 走北极经线。中点应在北极点。
        lat, lon = slerp(10.0, 30.0, -10.0, -150.0, 0.5)
        self.assertAlmostEqual(lat, 90.0 - 10.0 + 0.0, delta=1e-9)  # 已过极点
        # 前半段在 lon=30 经线上
        _, lon_q1 = slerp(10.0, 30.0, -10.0, -150.0, 0.25)
        self.assertAlmostEqual(lon_q1, 30.0, places=9)
        # 后半段在 lon=-150 经线上
        _, lon_q3 = slerp(10.0, 30.0, -10.0, -150.0, 0.75)
        self.assertAlmostEqual(lon_q3, -150.0, places=9)

    def test_southern_hemisphere_convention(self):
        # lat1 < 0 走南极经线
        lat, _ = slerp(-10.0, 30.0, 10.0, -150.0, 0.5)
        self.assertAlmostEqual(lat, -80.0, delta=1e-9)

    def test_equator_antipodal(self):
        lat, _ = slerp(0.0, 0.0, 0.0, 180.0, 0.5)
        self.assertAlmostEqual(lat, 90.0, delta=1e-9)

    def test_pole_to_pole(self):
        lat, lon = slerp(90.0, 45.0, -90.0, 45.0, 0.5)
        self.assertAlmostEqual(lat, 0.0, places=12)
        self.assertAlmostEqual(lon, 45.0, places=12)

    def test_matches_reference(self):
        assert_close_to_ref(self, (10.0, 30.0), (-10.0, -150.0))
        assert_close_to_ref(self, (0.0, 0.0), (0.0, 180.0))
        assert_close_to_ref(self, (90.0, 45.0), (-90.0, 45.0))


class TestCoincident(unittest.TestCase):
    def test_same_point_all_t(self):
        for t in (0.0, 0.25, 0.5, 0.75, 1.0):
            self.assertEqual(slerp(25.0, 60.0, 25.0, 60.0, t), (25.0, 60.0))

    def test_matches_reference(self):
        assert_close_to_ref(self, (25.0, 60.0), (25.0, 60.0))


class TestNormalizeLon(unittest.TestCase):
    def test_range(self):
        self.assertEqual(normalize_lon(190.0), -170.0)
        self.assertEqual(normalize_lon(-190.0), 170.0)
        self.assertEqual(normalize_lon(180.0), -180.0)
        self.assertEqual(normalize_lon(540.0), -180.0)
        self.assertEqual(normalize_lon(0.0), 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
