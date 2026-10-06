"""benchmark_1h -- 1 小时素材帧率转换的耗时与内存峰值。

纯标准库；无真实像素管线，payload 为模拟引用（见说明）。
测三种用法:
  A. 流式 + 无 payload（纯调度层）
  B. 流式 + 1MiB/帧 payload 引用（模拟真实帧缓冲，内存仍应 O(1)）
  C. 全量列表化（frames 全部物化成 list）
耗时取 3 次最优；内存用 tracemalloc（CPython Python 层分配，不含解释器常驻）。
"""

import csv
import os
import time
import tracemalloc
from fractions import Fraction
from math import ceil

from fps_convert import Frame, convert_fps

SRC_RATE = Fraction(24000, 1001)   # 23.976 fps
DST_RATE = 25
DURATION = 3600                    # 秒
N_SRC = ceil(DURATION * SRC_RATE)  # 覆盖整 1 小时所需源帧数
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")

PAYLOAD_SIZE = 1024 * 1024


def stream_src(payload_size=0):
    payload = b"\0" * payload_size if payload_size else None
    for i in range(N_SRC):
        yield Frame(Fraction(i, 1) / SRC_RATE, payload)


def bench_stream(payload_size=0, repeats=3):
    best = None
    for _ in range(repeats):
        tracemalloc.start()
        t0 = time.perf_counter()
        n = 0
        for _ in convert_fps(stream_src(payload_size), SRC_RATE, DST_RATE,
                             duration=DURATION):
            n += 1
        dt = time.perf_counter() - t0
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        best = dt if best is None else min(best, dt)
    return n, best, peak


def bench_list(repeats=3):
    src = list(stream_src())
    src_size = sum(1 for _ in ())  # noqa
    import sys
    total = sys.getsizeof(src) + sum(sys.getsizeof(f) + sys.getsizeof(f.pts)
                                     for f in src)
    best = None
    for _ in range(repeats):
        tracemalloc.start()
        t0 = time.perf_counter()
        out = list(convert_fps(iter(src), SRC_RATE, DST_RATE, duration=DURATION))
        dt = time.perf_counter() - t0
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        best = dt if best is None else min(best, dt)
    return len(out), best, peak, total, len(src)


def collect_drift_sampled():
    """每隔约 2 分钟采一次累计偏移，写入 CSV（1 小时数据，约 31 个采样点 + 首尾）。"""
    step = 3000
    rows, cum, max_abs_phase, max_abs_cum, n = [], Fraction(0), Fraction(0), Fraction(0), 0
    for f in convert_fps(stream_src(), SRC_RATE, DST_RATE, duration=DURATION):
        cum += f.phase_error
        max_abs_phase = max(max_abs_phase, abs(f.phase_error))
        max_abs_cum = max(max_abs_cum, abs(cum))
        if (f.target_index % step == 0 or f.target_index < 3 or
                f.target_index >= DST_RATE * DURATION - 3):
            rows.append((f.target_index, float(f.pts), float(cum) * 1000,
                         float(f.phase_error) * 1000))
        n += 1
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "drift_1h_sampled.csv")
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["target_index", "target_pts_s", "cum_phase_error_ms",
                    "frame_phase_error_ms"])
        w.writerows(rows)
    return n, rows, float(max_abs_phase) * 1000, float(max_abs_cum) * 1000, float(cum) * 1000


def main():
    print(f"素材: {SRC_RATE} fps -> {DST_RATE} fps, 时长 {DURATION}s, "
          f"源 {N_SRC} 帧 (~{N_SRC/SRC_RATE:.3f}s 覆盖)")
    ts_ms = float(Fraction(1, 1) / SRC_RATE) * 1000
    print(f"源帧间隔 Ts = {ts_ms:.4f} ms；单帧相位误差上界: 中间帧 {ts_ms/2:.4f} ms, "
          f"尾部 {ts_ms:.4f} ms（与帧号/时长无关）\n")

    n1, t1, p1 = bench_stream(0)
    print(f"A. 流式/无payload : {n1} 帧, {t1:7.3f} s  "
          f"({n1/t1:8.0f} 帧/秒), tracemalloc 峰值 {p1/1024:8.1f} KiB")
    n2, t2, p2 = bench_stream(PAYLOAD_SIZE)
    print(f"B. 流式/1MiB payload: {n2} 帧, {t2:7.3f} s  "
          f"({n2/t2:8.0f} 帧/秒), tracemalloc 峰值 {p2/1024:8.1f} KiB")
    n3, t3, p3, src_bytes, nsrc = bench_list()
    print(f"C. 全量列表/无payload: {n3} 帧, {t3:7.3f} s  "
          f"({n3/t3:8.0f} 帧/秒), 转换阶段 tracemalloc 峰值 {p3/1024:8.1f} KiB, "
          f"源列表常驻 {src_bytes/1024:8.1f} KiB ({nsrc} 帧)")

    n, rows, max_phase, max_cum, final_cum = collect_drift_sampled()
    print(f"\n累计偏移(相位误差累计和): max|.| = {max_cum:.4f} ms, "
          f"末值 = {final_cum:+.4f} ms；单帧 max|.| = {max_phase:.4f} ms")
    print(f"采样 {len(rows)} 行 -> {os.path.join(OUT_DIR, 'drift_1h_sampled.csv')}")
    print("\n采样点（帧号 / 时间 / 累计ms）:")
    for idx, pts_s, cum_ms, ph_ms in rows[:6]:
        print(f"  k={idx:6d}  t={pts_s:8.3f}s  cum={cum_ms:+9.4f} ms  e={ph_ms:+8.3f}")
    print("  ...")
    for idx, pts_s, cum_ms, ph_ms in rows[-4:]:
        print(f"  k={idx:6d}  t={pts_s:8.3f}s  cum={cum_ms:+9.4f} ms  e={ph_ms:+8.3f}")


if __name__ == "__main__":
    main()
