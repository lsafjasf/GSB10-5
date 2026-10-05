"""frc 库自测：整数/非整数倍率、升/降帧、首帧偏移、极短素材、漂移上界。"""
import unittest
from fractions import Fraction

from frc import FrameRateConverter, to_fraction


class TestRatioCases(unittest.TestCase):
    def test_integer_up_24_to_48(self):
        c = FrameRateConverter(24, 48, 100)
        self.assertEqual(c.num_dst, 200)
        # 每帧复制一次；恰好落在两源帧正中间的输出帧按"逢半进一"取后者，
        # 映射误差 = 半帧源间隔，仍在上界内；末帧钳位到最后一帧源画面
        for n in range(200):
            self.assertEqual(c.src_index_for(n), min((n + 1) // 2, 99))
            self.assertLessEqual(abs(c.mapping_error(n)), Fraction(1, 48))

    def test_integer_down_48_to_24(self):
        c = FrameRateConverter(48, 24, 100)
        self.assertEqual(c.num_dst, 50)
        for n in range(50):
            self.assertEqual(c.src_index_for(n), 2 * n)
            self.assertEqual(c.mapping_error(n), 0)

    def test_noninteger_up_24_to_30(self):
        c = FrameRateConverter(24, 30, 8)
        self.assertEqual(c.num_dst, 10)
        # 经典 24->30：每 4 源帧出 5 帧，第 3 源帧被复制
        got = [c.src_index_for(n) for n in range(10)]
        self.assertEqual(got, [0, 1, 2, 2, 3, 4, 5, 6, 6, 7])

    def test_noninteger_down_30_to_24(self):
        c = FrameRateConverter(30, 24, 10)
        self.assertEqual(c.num_dst, 8)
        got = [c.src_index_for(n) for n in range(8)]
        self.assertEqual(got, [0, 1, 3, 4, 5, 6, 8, 9])  # 每 5 丢 1

    def test_ntsc_rates(self):
        # 23.976 -> 29.97，必须用有理数才能不漂
        c = FrameRateConverter("24000/1001", "30000/1001", 24000)
        self.assertEqual(c.num_dst, 30000)
        bound = Fraction(1, 2) / to_fraction("24000/1001")
        for n in range(0, 30000, 7):  # 抽样验证上界
            self.assertLessEqual(abs(c.mapping_error(n)), bound)

    def test_monotonic_and_complete(self):
        # 源帧下标单调不减；升帧不丢帧、降帧不复制
        up = FrameRateConverter(24, 60, 500)
        idx = [up.src_index_for(n) for n in range(up.num_dst)]
        self.assertTrue(all(b >= a for a, b in zip(idx, idx[1:])))
        self.assertEqual(sorted(set(idx)), list(range(500)))  # 升帧无丢帧
        down = FrameRateConverter(60, 24, 500)
        idx = [down.src_index_for(n) for n in range(down.num_dst)]
        self.assertEqual(len(idx), len(set(idx)))  # 降帧无复制


class TestTimeline(unittest.TestCase):
    def test_zero_drift_by_construction(self):
        # 输出时间戳与目标时间轴逐帧对拍：偏差恒为 0
        c = FrameRateConverter(24, 30, 86400)  # 1 小时
        for n in (0, 1, 2, 999, 54321, c.num_dst - 1):
            self.assertEqual(c.timeline_drift(n), 0)
            self.assertEqual(c.out_pts(n), Fraction(n, 30))

    def test_mapping_error_bounded_one_hour(self):
        # 1 小时全量扫描：映射误差不随帧号增长，严格有界
        c = FrameRateConverter(24, 30, 86400)
        worst = c.max_abs_mapping_error()
        self.assertLessEqual(worst, c.theoretical_bound())
        self.assertLessEqual(worst, Fraction(1, 48))  # 半帧源间隔

    def test_end_time_offset(self):
        # 片尾时刻偏差：|末帧输出时刻 - 末帧源时刻| 有界（毫秒级，而非秒级）
        c = FrameRateConverter(24, 30, 86400)
        end_err = abs(c.out_pts(c.num_dst - 1) - c.src_pts(c.num_src - 1))
        self.assertLessEqual(end_err, Fraction(1, 48))


class TestEdgeCases(unittest.TestCase):
    def test_t0_offset(self):
        # 首帧起始时间不为零：输出时间轴整体平移，首帧时刻 == t0
        t0 = Fraction(7, 2)  # 3.5s
        c = FrameRateConverter(24, 30, 100, t0=t0)
        self.assertEqual(c.out_pts(0), t0)
        self.assertEqual(c.out_pts(c.num_dst - 1), t0 + Fraction(c.num_dst - 1, 30))
        c0 = FrameRateConverter(24, 30, 100)
        for n in range(c.num_dst):  # 映射与 t0=0 完全一致
            self.assertEqual(c.src_index_for(n), c0.src_index_for(n))

    def test_single_frame(self):
        # 极短素材：1 帧 @24fps 时长 1/24s，升到 60fps 为 2.5 帧 -> 3 帧
        c = FrameRateConverter(24, 60, 1, t0=Fraction(3, 2))
        self.assertEqual(c.num_dst, 3)
        self.assertEqual(c.out_pts(0), Fraction(3, 2))
        self.assertEqual(c.src_index_for(0), 0)
        # 1 帧 @60fps 降到 24fps 换算不足 1 帧，保底输出 1 帧（素材不消失）
        c2 = FrameRateConverter(60, 24, 1)
        self.assertEqual(c2.num_dst, 1)
        self.assertEqual(c2.src_index_for(0), 0)

    def test_two_frames_to_high_fps(self):
        c = FrameRateConverter(1, 60, 2)  # 2 秒素材升到 60fps
        self.assertEqual(c.num_dst, 120)
        self.assertEqual(c.src_index_for(0), 0)
        self.assertEqual(c.src_index_for(119), 1)

    def test_empty(self):
        c = FrameRateConverter(24, 30, 0)
        self.assertEqual(c.num_dst, 0)
        self.assertEqual(list(c.iter_map()), [])
        self.assertEqual(c.convert_frames([]), [])

    def test_convert_frames_payload(self):
        frames = [f"frame-{i}" for i in range(8)]
        c = FrameRateConverter(24, 30, 8)
        out = c.convert_frames(frames)
        self.assertEqual(out, [frames[k] for k in (0, 1, 2, 2, 3, 4, 5, 6, 6, 7)])

    def test_invalid_args(self):
        with self.assertRaises(ValueError):
            FrameRateConverter(0, 30, 10)
        with self.assertRaises(ValueError):
            FrameRateConverter(24, 30, -1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
