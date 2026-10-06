"""gsbav 自测：时间基换算、轨迹交叉、往返对拍、异常报告。"""
import os
import tempfile
import unittest
from fractions import Fraction

from gsbav import (Demuxer, Muxer, Rounding, Sample, TimeBase, interleave,
                   remux, rescale_q, tick_to_seconds, KIND_AUDIO, KIND_VIDEO)

TB_VIDEO = TimeBase(1, 12800)    # 常见视频时间基
TB_VIDEO_90K = TimeBase(1, 90000)
TB_AUDIO = TimeBase(1, 44100)    # 常见音频时间基


def gen_video(tb, seconds, fps=25, payload=64, start_pts=0):
    dur = tb.den // (tb.num * fps) if tb.num == 1 else tb.den // fps
    n = int(round(seconds * fps))
    for i in range(n):
        pts = start_pts + i * dur
        data = (b"V" + i.to_bytes(4, "little")) .ljust(payload, b".")[:payload]
        yield Sample(0, pts, pts, dur, i % fps == 0, data)


def gen_audio(tb, seconds, frame=1024, payload=32, start_pts=0):
    total = int(round(seconds * tb.den / tb.num))
    pts = start_pts
    while pts - start_pts < total:
        d = min(frame, total - (pts - start_pts))
        data = (b"A" + pts.to_bytes(8, "little")).ljust(payload, b".")[:payload]
        yield Sample(1, pts, pts, d, False, data)
        pts += d


class TestRescale(unittest.TestCase):
    """换算规则与取整方式的对拍数据。"""

    def test_known_values(self):
        cases = [
            # (tick, src, dst, 精确分数, NEAR_INF, FLOOR, CEIL)
            (1, TB_AUDIO, TB_VIDEO, Fraction(12800, 44100), 0, 0, 1),
            (2205, TB_AUDIO, TB_VIDEO, Fraction(640, 1), 640, 640, 640),   # 50ms 整除
            (441, TB_AUDIO, TB_VIDEO, Fraction(128, 1), 128, 128, 128),    # 10ms 整除
            (512, TB_VIDEO, TB_AUDIO, Fraction(512*44100, 12800), 1764, 1764, 1764),
            (513, TB_VIDEO, TB_AUDIO, Fraction(513*44100, 12800), 1767, 1767, 1768),
            (1, TB_VIDEO, TB_AUDIO, Fraction(44100, 12800), 3, 3, 4),
            (2, TB_VIDEO, TB_AUDIO, Fraction(2*44100, 12800), 7, 6, 7),    # 6.890625
            (256, TB_VIDEO, TB_AUDIO, Fraction(256*44100, 12800), 882, 882, 882),
            (128, TB_VIDEO, TB_AUDIO, Fraction(128*44100, 12800), 441, 441, 441),
        ]
        print("\n== 时间基换算对拍（NEAR_INF=四舍五入平局远离零）==")
        print(f"{'tick':>6} {'src':>8} {'dst':>8} {'精确值(秒*dst.den)':>20} "
              f"{'NEAR_INF':>9} {'FLOOR':>6} {'CEIL':>5}")
        for tick, src, dst, exact, near, fl, ce in cases:
            got_near = rescale_q(tick, src, dst, Rounding.NEAR_INF)
            got_fl = rescale_q(tick, src, dst, Rounding.FLOOR)
            got_ce = rescale_q(tick, src, dst, Rounding.CEIL)
            print(f"{tick:>6} 1/{src.den:<5} 1/{dst.den:<5} "
                  f"{float(exact):>20.6f} {got_near:>9} {got_fl:>6} {got_ce:>5}")
            self.assertEqual(got_near, near)
            self.assertEqual(got_fl, fl)
            self.assertEqual(got_ce, ce)

    def test_tie_away_from_zero(self):
        # 构造平局：1/2 tick。src=1/2, dst=1/1 -> 1*1*1/(2*1)=0.5
        self.assertEqual(rescale_q(1, TimeBase(1, 2), TimeBase(1, 1),
                                   Rounding.NEAR_INF), 1)
        self.assertEqual(rescale_q(-1, TimeBase(1, 2), TimeBase(1, 1),
                                   Rounding.NEAR_INF), -1)
        self.assertEqual(rescale_q(1, TimeBase(1, 2), TimeBase(1, 1),
                                   Rounding.FLOOR), 0)
        self.assertEqual(rescale_q(-1, TimeBase(1, 2), TimeBase(1, 1),
                                   Rounding.FLOOR), -1)

    def test_no_direct_tick_compare(self):
        # 同一真实时刻 1.0s：视频 12800 tick，音频 44100 tick，直接比数值必然错
        self.assertEqual(tick_to_seconds(12800, TB_VIDEO),
                         tick_to_seconds(44100, TB_AUDIO))


class MuxTestBase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.p = lambda name: os.path.join(self.dir.name, name)

    def tearDown(self):
        self.dir.cleanup()

    def mux_file(self, name, tracks, sample_lists, expected=(KIND_VIDEO, KIND_AUDIO)):
        path = self.p(name)
        with Muxer(path, expected_kinds=expected) as mux:
            for kind, tb, codec in tracks:
                mux.add_track(kind, tb, codec)
            merged = interleave([(tracks[i][1], iter(sl))
                                 for i, sl in enumerate(sample_lists) if sl])
            for s in merged:
                mux.write_sample(s.track_id, s.pts, s.dts, s.duration,
                                 s.data, s.keyframe)
        return path, mux.report


class TestSingleTrack(MuxTestBase):
    def test_single_track_roundtrip(self):
        v = list(gen_video(TB_VIDEO, 4.0))
        path, rep = self.mux_file("single.gav",
                                  [(KIND_VIDEO, TB_VIDEO, "rawv")], [v],
                                  expected=(KIND_VIDEO,))
        out = list(Demuxer(path).samples())
        self.assertEqual(len(out), len(v))
        self.assertTrue(all(a == b for a, b in zip(v, out)))
        self.assertEqual(rep.warnings, [])
        self.assertEqual(rep.intersection_start_sec, Fraction(0))
        self.assertEqual(rep.intersection_end_sec, Fraction(4))
        print(f"\n== 单轨迹 == 样本数={rep.tracks[0].nb_samples} "
              f"区间=[{float(rep.intersection_start_sec)}, "
              f"{float(rep.intersection_end_sec)})s 警告={rep.warnings}")


class TestDualTrackAligned(MuxTestBase):
    def test_aligned_diff_timebases(self):
        v = list(gen_video(TB_VIDEO, 10.0))
        a = list(gen_audio(TB_AUDIO, 10.0))
        path, rep = self.mux_file("aligned.gav",
                                  [(KIND_VIDEO, TB_VIDEO, "rawv"),
                                   (KIND_AUDIO, TB_AUDIO, "rawa")], [v, a])
        # 交叉区间：起点取最大、终点取最小（精确有理数）
        self.assertEqual(rep.warnings, [])
        self.assertEqual(rep.intersection_start_sec, Fraction(0))
        self.assertEqual(rep.intersection_end_sec, Fraction(10))
        # 交叉边界换算回各轨迹时间基
        v_start = rescale_q(0, TimeBase(1, 1), TB_VIDEO)
        v_end = rescale_q(10, TimeBase(1, 1), TB_VIDEO)
        a_start = rescale_q(0, TimeBase(1, 1), TB_AUDIO)
        a_end = rescale_q(10, TimeBase(1, 1), TB_AUDIO)
        print(f"\n== 双轨迹对齐（tb 1/12800 vs 1/44100）==")
        print(f"视频: pts [{rep.tracks[0].start_pts}, {rep.tracks[0].end_pts}) "
              f"= [{rep.tracks[0].start_sec}, {rep.tracks[0].end_sec})s")
        print(f"音频: pts [{rep.tracks[1].start_pts}, {rep.tracks[1].end_pts}) "
              f"= [{rep.tracks[1].start_sec}, {rep.tracks[1].end_sec})s")
        print(f"交叉区间(秒): [{rep.intersection_start_sec}, "
              f"{rep.intersection_end_sec})")
        print(f"交叉区间换算: 视频tick [{v_start}, {v_end}), 音频tick [{a_start}, {a_end})")
        self.assertEqual((v_start, v_end, a_start, a_end), (0, 128000, 0, 441000))

    def test_offset_tracks_intersection(self):
        # 视频从 0s 开始 10s；音频从 0.5s 开始 10s -> 交叉 [0.5, 10)
        v = list(gen_video(TB_VIDEO_90K, 10.0))
        a = list(gen_audio(TB_AUDIO, 10.0, start_pts=22050))  # 22050/44100 = 0.5s
        path, rep = self.mux_file("offset.gav",
                                  [(KIND_VIDEO, TB_VIDEO_90K, "rawv"),
                                   (KIND_AUDIO, TB_AUDIO, "rawa")], [v, a])
        self.assertEqual(rep.intersection_start_sec, Fraction(1, 2))
        self.assertEqual(rep.intersection_end_sec, Fraction(10))
        # 交叉边界换算回视频 1/90000：0.5s -> 45000，10s -> 900000
        self.assertEqual(rescale_q(1, TimeBase(1, 2), TB_VIDEO_90K), 45000)
        self.assertEqual(rescale_q(10, TimeBase(1, 1), TB_VIDEO_90K), 900000)
        mism = [w for w in rep.warnings if w.startswith("DURATION_MISMATCH")]
        self.assertEqual(len(mism), 0)  # 时长均为 10s，仅起点错开，不报时长不一致
        print(f"\n== 起点错开（视频0s起/音频0.5s起，tb 1/90000 vs 1/44100）==")
        print(f"交叉区间(秒): [{rep.intersection_start_sec}, "
              f"{rep.intersection_end_sec}) = "
              f"[{float(rep.intersection_start_sec)}, {float(rep.intersection_end_sec)})")
        print(f"警告: {rep.warnings}")


class TestRoundtrip(MuxTestBase):
    def test_demux_remux_identical(self):
        v = list(gen_video(TB_VIDEO, 6.0, payload=96))
        a = list(gen_audio(TB_AUDIO, 6.0, payload=48))
        src, _ = self.mux_file("src.gav",
                               [(KIND_VIDEO, TB_VIDEO, "rawv"),
                                (KIND_AUDIO, TB_AUDIO, "rawa")], [v, a])
        dst = self.p("dst.gav")
        remux(src, dst, expected_kinds=(KIND_VIDEO, KIND_AUDIO))
        # 对拍1：文件字节完全一致
        with open(src, "rb") as f:
            b1 = f.read()
        with open(dst, "rb") as f:
            b2 = f.read()
        self.assertEqual(b1, b2)
        # 对拍2：逐样本比对（数据 + 全部时间信息）
        s1 = list(Demuxer(src).samples())
        s2 = list(Demuxer(dst).samples())
        self.assertEqual(len(s1), len(s2))
        diffs = sum(1 for x, y in zip(s1, s2) if x != y)
        self.assertEqual(diffs, 0)
        print(f"\n== 解封装再封装对拍 == 样本数={len(s1)} 不一致样本={diffs} "
              f"文件字节一致={b1 == b2} 文件大小={len(b1)}B")


class TestAnomalies(MuxTestBase):
    def test_duration_mismatch_reported_not_padded(self):
        v = list(gen_video(TB_VIDEO, 10.0))
        a = list(gen_audio(TB_AUDIO, 9.5))   # 音频短 0.5s
        path, rep = self.mux_file("mismatch.gav",
                                  [(KIND_VIDEO, TB_VIDEO, "rawv"),
                                   (KIND_AUDIO, TB_AUDIO, "rawa")], [v, a])
        mism = [w for w in rep.warnings if w.startswith("DURATION_MISMATCH")]
        self.assertEqual(len(mism), 1)
        # 不静默补齐：样本数与原始一致
        out_v = [s for s in Demuxer(path).samples() if s.track_id == 0]
        out_a = [s for s in Demuxer(path).samples() if s.track_id == 1]
        self.assertEqual(len(out_v), len(v))
        self.assertEqual(len(out_a), len(a))
        # 交叉终点 = 音频终点 9.5s
        self.assertEqual(rep.intersection_end_sec, Fraction(19, 2))
        print(f"\n== 时长不一致 == 视频10.000s 音频9.500s "
              f"交叉=[{float(rep.intersection_start_sec)}, "
              f"{float(rep.intersection_end_sec)})s")
        print(f"警告: {mism[0]}")
        print(f"样本数保持不变: 视频 {len(out_v)}/{len(v)}, 音频 {len(out_a)}/{len(a)}")

    def test_missing_track_reported(self):
        v = list(gen_video(TB_VIDEO, 2.0))
        path, rep = self.mux_file("missing.gav",
                                  [(KIND_VIDEO, TB_VIDEO, "rawv")], [v],
                                  expected=(KIND_VIDEO, KIND_AUDIO))
        self.assertIn("MISSING_TRACK: audio", rep.warnings)
        # 解封装侧同样能检出
        rep2 = Demuxer(path).report(expected_kinds=(KIND_VIDEO, KIND_AUDIO))
        self.assertIn("MISSING_TRACK: audio", rep2.warnings)
        print(f"\n== 缺轨迹 == 警告: {rep.warnings} / 解封装侧: {rep2.warnings}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
