"""avmux 自测（标准库 unittest）。

运行：python3 selftest.py -v
覆盖：
1. 时间基换算取整（nearest/down，正负数）
2. 单轨迹封装/解封装（音频-only）
3. 双轨迹对齐（1.0s，不同时间基但时长相等）
4. 双轨迹时间基不同（48k vs 90k，逐样本取整核对）
5. 轨迹长度不一致 -> warning，不补齐
6. 缺轨 -> error 报告（非 strict 继续，strict 抛错）
7. 解封装再封装逐样本对拍（时间戳 + payload + keyframe）
8. CRC 损坏检测
9. 交叉区间计算核对（含起点偏移）
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest
import zlib
from fractions import Fraction

from avmux import (
    AUDIO,
    MICROSECOND_TIME_BASE,
    ROUND_DOWN,
    ROUND_NEAREST,
    MediaFile,
    Sample,
    TimeBase,
    Track,
    convert_exact,
    convert_ticks,
    demux,
    mux,
)
from avmux.format import END_MAGIC, HEADER_SIZE, TRAILER_SIZE
from avmux.report import ERROR, WARNING, analyze
import samples

TB_A = TimeBase(1, 48000)
TB_V = TimeBase(1, 90000)


def roundtrip(media: MediaFile, required=("audio", "video")):
    buf = io.BytesIO()
    report = mux(media, buf, required=required)
    buf.seek(0)
    media2, report2 = demux(buf, required=required)
    return media2, report, report2


def compare_media(m1: MediaFile, m2: MediaFile, t: unittest.TestCase):
    """逐轨迹逐样本对拍。"""
    t.assertEqual(len(m1.tracks), len(m2.tracks))
    t.assertEqual(m1.creation_time, m2.creation_time)
    for tr1, tr2 in zip(m1.tracks, m2.tracks):
        t.assertEqual(tr1.track_type, tr2.track_type)
        t.assertEqual(str(tr1.time_base), str(tr2.time_base))
        t.assertEqual(tr1.name, tr2.name)
        t.assertEqual(len(tr1.samples), len(tr2.samples))
        for s1, s2 in zip(tr1.samples, tr2.samples):
            t.assertEqual(s1.pts, s2.pts)
            t.assertEqual(s1.dts, s2.dts)
            t.assertEqual(s1.duration, s2.duration)
            t.assertEqual(s1.keyframe, s2.keyframe)
            t.assertEqual(s1.data, s2.data)


class TimeBaseTests(unittest.TestCase):
    def test_conversion_rounding(self):
        # 1 tick @ 1/48000 s = 20.8333... us
        self.assertEqual(convert_exact(1, TB_A, MICROSECOND_TIME_BASE),
                         Fraction(125, 6))
        self.assertEqual(convert_ticks(1, TB_A, MICROSECOND_TIME_BASE, ROUND_NEAREST), 21)
        self.assertEqual(convert_ticks(1, TB_A, MICROSECOND_TIME_BASE, ROUND_DOWN), 20)
        # 1024 音频 tick = 21333.333... us
        self.assertEqual(convert_ticks(1024, TB_A, MICROSECOND_TIME_BASE), 21333)
        self.assertEqual(convert_ticks(1024, TB_A, MICROSECOND_TIME_BASE, ROUND_DOWN), 21333)
        # 视频 3003 tick @ 1/90000 s = 33366.666... us
        self.assertEqual(convert_ticks(3003, TB_V, MICROSECOND_TIME_BASE), 33367)
        self.assertEqual(convert_ticks(3003, TB_V, MICROSECOND_TIME_BASE, ROUND_DOWN), 33366)
        # 半数位恰好 .5：nearest 向上（half-up），down 向下
        # 48000 -> 1/100000: 1 tick = 20.8333 us；找一个恰 .5 的：
        tb_x = TimeBase(1, 2)  # 1 tick = 500000 us
        tb_y = TimeBase(1, 4)  # 1 tick = 250000 us；1 tick(tb_x)=2 tick(tb_y)，整数
        self.assertEqual(convert_ticks(1, tb_x, tb_y), 2)
        tb_z = TimeBase(1, 8)
        self.assertEqual(convert_ticks(1, tb_x, tb_z), 4)

    def test_negative_rounding_symmetric(self):
        self.assertEqual(convert_ticks(-1, TB_A, MICROSECOND_TIME_BASE), -21)
        self.assertEqual(convert_ticks(-1, TB_A, MICROSECOND_TIME_BASE, ROUND_DOWN), -21)

    def test_bad_timebase(self):
        with self.assertRaises(ValueError):
            TimeBase(0, 1000)
        with self.assertRaises(ValueError):
            TimeBase(1, 0)


class SingleTrackTests(unittest.TestCase):
    def test_audio_only(self):
        media = MediaFile(tracks=[samples.make_audio_track(47)], creation_time=1234)
        media2, rep, rep2 = roundtrip(media, required=(AUDIO,))
        compare_media(media, media2, self)
        self.assertTrue(rep.ok and rep2.ok)
        # 默认 required=双轨时：缺视频必须报告
        _, rep_both, _ = roundtrip(media, required=("audio", "video"))
        self.assertTrue(any(i.severity == ERROR and i.code == "missing_track"
                            for i in rep_both.issues))
        self.assertIsNone(media2.get_track("video"))  # 没有被静默补齐

    def test_strict_missing_raises(self):
        media = MediaFile(tracks=[samples.make_audio_track(2)])
        buf = io.BytesIO()
        with self.assertRaises(Exception):
            mux(media, buf, strict=True)


class DualTrackAlignedTests(unittest.TestCase):
    def test_aligned_one_second(self):
        # 音频 50 帧 * 20 tick @ 1/1000 = 1.0s；视频 30 帧 * 3000 tick @ 1/90000 = 1.0s
        media = MediaFile(tracks=[
            samples.make_audio_track(50, time_base=TimeBase(1, 1000), frame_ticks=20),
            samples.make_video_track(30, time_base=TimeBase(1, 90000), frame_ticks=3000),
        ])
        # 50*20/1000 = 1.0s；30*3000/90000 = 1.0s
        media2, rep, rep2 = roundtrip(media)
        self.assertTrue(rep.ok and rep2.ok)
        self.assertEqual(rep.overlap_start_us, 0)
        self.assertEqual(rep.overlap_end_us, 1_000_000)
        self.assertEqual(rep.overlap_duration_us, 1_000_000)
        compare_media(media, media2, self)


class DifferentTimeBaseTests(unittest.TestCase):
    def test_real_48k_vs_90k_mismatch_reported(self):
        # 音频 1024 tick/帧，视频 3003 tick/帧（29.97fps）。
        # 30 视频帧 = 1001000 us；最近音频时长 47*21333.33 = 1002666.67 us。
        media = samples.make_media(audio_frames=47, video_frames=30)
        media2, rep, rep2 = roundtrip(media)
        compare_media(media, media2, self)
        warn = [i for i in rep.issues if i.code == "duration_mismatch"]
        self.assertEqual(len(warn), 1)
        # 音频时长 47*1024 = 48128 tick；视频 30*3003 = 90090 tick
        span_a = next(s for s in rep.spans if s.track_type == "audio")
        span_v = next(s for s in rep.spans if s.track_type == "video")
        self.assertEqual((span_a.start_us, span_a.end_us), (0, 1_002_666))
        self.assertEqual((span_v.start_us, span_v.end_us), (0, 1_001_000))
        # 交叉 = 视频区间（较短者）
        self.assertEqual(rep.overlap_start_us, 0)
        self.assertEqual(rep.overlap_end_us, 1_001_000)
        self.assertEqual(rep.overlap_duration_us, 1_001_000)
        # 报告中保留精确分数，展示取整误差
        self.assertEqual(span_a.end_us_exact, Fraction(3_008_000, 3))

    def test_sample_level_conversion_table(self):
        # 逐样本把音频/视频 dts 换算到 us，验证取整口径
        track = samples.make_audio_track(3)
        got = [track.time_base.ticks_to_us(s.dts) for s in track.samples]
        self.assertEqual(got, [0, 21333, 42667])
        track = samples.make_video_track(3)
        got = [track.time_base.ticks_to_us(s.dts) for s in track.samples]
        self.assertEqual(got, [0, 33367, 66733])


class LengthMismatchTests(unittest.TestCase):
    def test_shorter_video(self):
        media = samples.make_media(audio_frames=20, video_frames=5)
        media2, rep, rep2 = roundtrip(media)
        self.assertTrue(any(i.code == "duration_mismatch" and i.severity == WARNING
                            for i in rep.issues))
        # 样本数保持原样，绝不在任一侧补样本
        self.assertEqual(len(media2.get_track("audio").samples), 20)
        self.assertEqual(len(media2.get_track("video").samples), 5)

    def test_start_offsets_crossing(self):
        # 音频从 5000us 起（1/1000 基 5 tick），视频从 0 起
        media = samples.make_media(
            audio_frames=10, video_frames=6,
            audio_time_base=TimeBase(1, 1000), audio_frame_ticks=10,
            video_time_base=TimeBase(1, 90000), video_frame_ticks=3000,
            audio_start_ticks=5,
        )
        # 音频 [5000, 105000)us；视频 [0, 200000)us
        rep = analyze(media)
        self.assertEqual(rep.overlap_start_us, 5000)
        self.assertEqual(rep.overlap_end_us, 105000)


class RoundTripFileTests(unittest.TestCase):
    def test_file_path_roundtrip(self):
        media = samples.make_media(audio_frames=7, video_frames=4)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.avmx")
            mux(media, path)
            media2, rep = demux(path)
        compare_media(media, media2, self)

    def test_crc_corruption_detected(self):
        media = samples.make_media(audio_frames=3, video_frames=2)
        buf = io.BytesIO()
        mux(media, buf)
        raw = bytearray(buf.getvalue())
        raw[HEADER_SIZE + 40] ^= 0xFF
        from avmux.format import FormatError
        with self.assertRaises(FormatError):
            demux(io.BytesIO(bytes(raw)))
        # 尾巴被裁
        with self.assertRaises(FormatError):
            demux(io.BytesIO(bytes(raw[:-1])))

    def test_empty_and_duplicate_tracks(self):
        empty = MediaFile(tracks=[Track("audio", TB_A)])
        rep = analyze(empty, require=("audio",))
        self.assertTrue(any(i.code == "empty_track" and i.severity == ERROR
                            for i in rep.issues))
        dup = MediaFile(tracks=[samples.make_audio_track(1), samples.make_audio_track(1)])
        rep = analyze(dup)
        self.assertTrue(any(i.code == "duplicate_track" for i in rep.issues))


if __name__ == "__main__":
    unittest.main(verbosity=2)
