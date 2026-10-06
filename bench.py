"""1 小时合成素材的封装/解封装耗时与内存峰值基准（仅标准库）。

素材：
- 视频 25fps，时间基 1/12800（帧时长 512 tick），90000 帧，每帧 512B
- 音频 44100Hz，时间基 1/44100（帧时长 1024 tick），共 158760000 采样，
  前 155039 帧各 1024，最后一帧 64（精确 3600s），每帧载荷 256B
全程流式：生成/封装/解封装均不在内存中保留整段素材。
"""
import hashlib
import os
import resource
import sys
import time
import tracemalloc

from gsbav import (Demuxer, Muxer, Rounding, TimeBase, interleave, remux,
                   rescale_q, KIND_AUDIO, KIND_VIDEO)

SECONDS = 3600
VIDEO_TB = TimeBase(1, 12800)
AUDIO_TB = TimeBase(1, 44100)
FPS = 25
AUDIO_FRAME = 1024
VFRAME_N = SECONDS * FPS          # 90000
AUDIO_TOTAL = 44100 * SECONDS     # 158760000
AFRAME_N = (AUDIO_TOTAL + AUDIO_FRAME - 1) // AUDIO_FRAME  # 155040


def video_gen():
    dur = VIDEO_TB.den // FPS  # 512
    for i in range(VFRAME_N):
        pts = i * dur
        data = (b"V" + i.to_bytes(4, "little")).ljust(512, b".")[:512]
        yield 0, pts, pts, dur, True, data


def audio_gen():
    pts = 0
    for i in range(AFRAME_N):
        d = min(AUDIO_FRAME, AUDIO_TOTAL - pts)
        data = (b"A" + i.to_bytes(4, "little")).ljust(256, b".")[:256]
        yield 1, pts, pts, d, False, data
        pts += d


def mux_1h(path):
    with Muxer(path, expected_kinds=(KIND_VIDEO, KIND_AUDIO)) as mux:
        mux.add_track(KIND_VIDEO, VIDEO_TB, "rawv25")
        mux.add_track(KIND_AUDIO, AUDIO_TB, "rawa44100")
        # 按 dts 真实秒数归并（Fraction 比较，不直接比 tick）
        merged = interleave([
            (VIDEO_TB, _as_samples(video_gen())),
            (AUDIO_TB, _as_samples(audio_gen())),
        ])
        for s in merged:
            mux.write_sample(s.track_id, s.pts, s.dts, s.duration, s.data, s.keyframe)
    return mux.report


class _S:
    __slots__ = ("track_id", "pts", "dts", "duration", "keyframe", "data")

    def __init__(self, *a):
        self.track_id, self.pts, self.dts, self.duration, self.keyframe, self.data = a


def _as_samples(gen):
    for tid, pts, dts, dur, kf, data in gen:
        yield _S(tid, pts, dts, dur, kf, data)


def demux_hash(path):
    d = Demuxer(path)
    h = hashlib.sha256()
    counts = {0: 0, 1: 0}
    for s in d.samples():
        counts[s.track_id] += 1
        h.update(s.track_id.to_bytes(4, "little"))
        h.update(s.pts.to_bytes(8, "little", signed=True))
        h.update(s.dts.to_bytes(8, "little", signed=True))
        h.update(s.duration.to_bytes(8, "little", signed=True))
        h.update(s.data)
    return h.hexdigest(), counts, d.report(expected_kinds=(KIND_VIDEO, KIND_AUDIO))


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(outdir, exist_ok=True)
    src = os.path.join(outdir, "hour_src.gav")
    dst = os.path.join(outdir, "hour_dst.gav")

    tracemalloc.start()
    t0 = time.perf_counter()
    rep = mux_1h(src)
    t1 = time.perf_counter()
    _, py_peak_mux = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    mux_dt = t1 - t0

    tracemalloc.start()
    t0 = time.perf_counter()
    h1, counts, drep = demux_hash(src)
    t1 = time.perf_counter()
    _, py_peak_dmx = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    dmx_dt = t1 - t0

    t0 = time.perf_counter()
    remux(src, dst, expected_kinds=(KIND_VIDEO, KIND_AUDIO))
    t1 = time.perf_counter()
    remux_dt = t1 - t0
    h2, counts2, _ = demux_hash(dst)

    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    size_mb = os.path.getsize(src) / 1e6

    print("== 1 小时素材封装/解封装基准 ==")
    print(f"文件大小: {size_mb:.1f} MB")
    print(f"样本数: 视频 {counts[0]} 帧, 音频 {counts[1]} 帧")
    print(f"视频区间: [{drep.tracks[0].start_pts}, {drep.tracks[0].end_pts}) tick "
          f"= [{drep.tracks[0].start_sec}, {drep.tracks[0].end_sec})s")
    print(f"音频区间: [{drep.tracks[1].start_pts}, {drep.tracks[1].end_pts}) tick "
          f"= [{drep.tracks[1].start_sec}, {drep.tracks[1].end_sec})s")
    print(f"交叉区间(秒): [{drep.intersection_start_sec}, {drep.intersection_end_sec})")
    # 交叉边界在两条轨迹时间基上的换算
    is0 = rep.intersection_start_sec
    ie0 = rep.intersection_end_sec
    for tb, name in ((VIDEO_TB, "视频"), (AUDIO_TB, "音频")):
        s_tick = rescale_q(is0.numerator, TimeBase(1, is0.denominator), tb)
        e_tick = rescale_q(ie0.numerator, TimeBase(1, ie0.denominator), tb)
        print(f"交叉边界换算->{name}(1/{tb.den}): [{s_tick}, {e_tick}) tick")
    print(f"警告: {drep.warnings}")
    print()
    print(f"封装耗时:   {mux_dt:7.3f} s  Python对象峰值 {py_peak_mux/1e6:7.2f} MB")
    print(f"解封装耗时: {dmx_dt:7.3f} s  Python对象峰值 {py_peak_dmx/1e6:7.2f} MB")
    print(f"再封装耗时: {remux_dt:7.3f} s")
    print(f"进程峰值RSS(含解释器): {rss_kb/1024:7.1f} MB")
    print()
    print(f"往返对拍: 样本数一致={counts == counts2}  样本流SHA256一致={h1 == h2}")
    with open(src, "rb") as a, open(dst, "rb") as b:
        same = a.read() == b.read()
    print(f"往返对拍: 文件字节一致={same}")
    if h1 != h2 or counts != counts2 or not same:
        sys.exit(1)


if __name__ == "__main__":
    main()
