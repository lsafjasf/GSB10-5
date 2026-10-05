"""1 小时素材封装/解封装基准。

素材：音频 48kHz、1024 tick/帧（168750 帧，恰好 3600.0s）；
      视频 90kHz 时间基、3000 tick/帧（108000 帧，恰好 3600.0s）。
payload：音频 32B/帧、视频 64B/帧（总数据约 12.3MB）。

指标：各阶段 wall 时间、tracemalloc 增量峰值、进程 RSS 峰值增量（Linux）。

运行：python3 benchmark.py
"""

from __future__ import annotations

import gc
import os
import platform
import tempfile
import time
import tracemalloc

from avmux import demux, mux
import samples

SECONDS = 3600
AUDIO_FRAMES = SECONDS * 48000 // 1024          # 168750
VIDEO_FRAMES = SECONDS * 90000 // 3000          # 108000
assert AUDIO_FRAMES * 1024 == SECONDS * 48000
assert VIDEO_FRAMES * 3000 == SECONDS * 90000


def peak_rss_bytes() -> int:
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0


def rss_bytes() -> int:
    try:
        with open("/proc/self/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0


def mb(n: float) -> str:
    return f"{n / 1024 / 1024:.2f} MiB"


def main():
    print(f"平台: {platform.platform()}  Python {platform.python_version()}")
    print(f"素材: 音频 {AUDIO_FRAMES} 帧 + 视频 {VIDEO_FRAMES} 帧, 时长 {SECONDS}s")

    gc.collect()
    t0 = time.perf_counter()
    media = samples.make_media(audio_frames=AUDIO_FRAMES, video_frames=VIDEO_FRAMES,
                           video_frame_ticks=3000)
    t1 = time.perf_counter()
    print(f"生成素材: {t1 - t0:.3f}s, RSS {mb(rss_bytes())}")

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "one_hour.avmx")

        # ---- mux ----
        gc.collect()
        rss0, hwm0 = rss_bytes(), peak_rss_bytes()
        tracemalloc.start()
        t0 = time.perf_counter()
        rep = mux(media, path)
        t1 = time.perf_counter()
        cur, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        mux_wall = t1 - t0
        size = os.path.getsize(path)
        print(f"\n封装(mux):")
        print(f"  耗时            {mux_wall:.3f} s")
        print(f"  输出文件        {mb(size)}")
        print(f"  tracemalloc峰值 {mb(peak)}")
        print(f"  RSS峰值增量     {mb(max(0, peak_rss_bytes() - hwm0))}")
        print(f"  报告            {len(rep.issues)} 条 "
              f"(时长不一致: {any(i.code == 'duration_mismatch' for i in rep.issues)})")

        # ---- demux ----
        gc.collect()
        rss0, hwm0 = rss_bytes(), peak_rss_bytes()
        tracemalloc.start()
        t0 = time.perf_counter()
        media2, rep2 = demux(path)
        t1 = time.perf_counter()
        cur, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        demux_wall = t1 - t0
        print(f"\n解封装(demux):")
        print(f"  耗时            {demux_wall:.3f} s")
        print(f"  tracemalloc峰值 {mb(peak)}  (含整个 MediaFile 对象图)")
        print(f"  RSS峰值增量     {mb(max(0, peak_rss_bytes() - hwm0))}")

        # ---- 对拍 ----
        t0 = time.perf_counter()
        mismatches = 0
        for tr1, tr2 in zip(media.tracks, media2.tracks):
            assert tr1.time_base == tr2.time_base
            for s1, s2 in zip(tr1.samples, tr2.samples):
                if (s1.pts, s1.dts, s1.duration, s1.keyframe, s1.data) != (
                    s2.pts, s2.dts, s2.duration, s2.keyframe, s2.data
                ):
                    mismatches += 1
        t1 = time.perf_counter()
        print(f"\n对拍: {t1 - t0:.3f}s, 不一致样本数 {mismatches}")

        # ---- 再封装字节级一致性 ----
        path2 = os.path.join(d, "remux.avmx")
        mux(media2, path2)
        with open(path, "rb") as f1, open(path2, "rb") as f2:
            same = f1.read() == f2.read()
        print(f"再封装字节级一致: {same}")


if __name__ == "__main__":
    main()
