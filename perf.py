"""perf - 5 分钟音频的耗时与内存峰值测试。运行: python3 perf.py"""

import math
import random
import resource
import time
from array import array

import audiots

SR = 16000
DUR = 300.0  # 5 分钟


def gen_signal(sr=SR, dur=DUR):
    """确定性测试信号: 双声部谐波 + 颤音 + 轻噪声, 峰值约 0.8."""
    n = int(sr * dur)
    rng = random.Random(42)
    out = array("d", [0.0]) * n
    ph1 = ph2 = 0.0
    two_pi = 2.0 * math.pi
    for i in range(n):
        t = i / sr
        f1 = 220.0 * (1.0 + 0.01 * math.sin(two_pi * 5.0 * t))   # 颤音
        f2 = 330.0
        ph1 += two_pi * f1 / sr
        ph2 += two_pi * f2 / sr
        if ph1 > two_pi:
            ph1 -= two_pi
        if ph2 > two_pi:
            ph2 -= two_pi
        out[i] = (0.35 * math.sin(ph1) + 0.15 * math.sin(2.0 * ph1)
                  + 0.2 * math.sin(ph2) + 0.1 * math.sin(3.0 * ph2)
                  + 0.02 * (rng.random() - 0.5))
    return out


def main():
    print("生成 5 分钟测试信号 (16 kHz 单声道)...")
    t0 = time.perf_counter()
    x = gen_signal()
    print("  样本数 %d (%.1f s), 生成耗时 %.1f s" % (len(x), len(x) / SR, time.perf_counter() - t0))

    # 组合处理: tempo=1.25, pitch=+2 半音 (重采样 + WSOLA 都跑到)
    r = audiots.semitones_to_ratio(2.0)
    t1 = time.perf_counter()
    y1 = audiots.resample(x, r)
    t2 = time.perf_counter()
    y2 = audiots.time_stretch(y1, 1.25 * r, SR)
    t3 = time.perf_counter()
    rss_peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    expect = round(len(x) * 1.25)
    print()
    print("## 5 分钟音频 (16 kHz mono, %d 样本) 组合处理 tempo=1.25, pitch=+2st" % len(x))
    print()
    print("| 阶段 | 耗时 s |")
    print("|---|---|")
    print("| 重采样 r=1.1225 (%d -> %d 样本) | %.1f |" % (len(x), len(y1), t2 - t1))
    print("| WSOLA x1.4031 (%d -> %d 样本) | %.1f |" % (len(y1), len(y2), t3 - t2))
    print("| 合计 | %.1f |" % (t3 - t1,))
    print()
    print("| 指标 | 数值 |")
    print("|---|---|")
    print("| 输出时长 (实测/期望) | %.3f s / %.3f s |" % (len(y2) / SR, expect / SR))
    print("| 进程 RSS 峰值 (ru_maxrss) | %.1f MB |" % (rss_peak / 1024.0,))
    print("| 实时倍率 (音频时长/耗时) | %.2fx |" % (DUR / (t3 - t1),))


if __name__ == "__main__":
    main()
