"""色调曲线自测：单调性校验、端点边界、LUT 与逐点对拍、1 万张图性能。

运行: python3 selftest.py
"""
import random
import resource
import sys
import time
import tracemalloc

from tonecurve import (DEPTH, BuildReport, apply_lut, build_lut, map_pointwise)

W, H, C = 256, 256, 3          # 测试图尺寸: 256x256 RGB
N_IMAGES = 10000               # 性能测试图片数量


def check_case(name, points, expect_raw_monotonic, expect_endpoints_ok=True):
    print("=" * 66)
    print("用例: %s" % name)
    print("控制点: %s" % (points,))
    lut, rep = build_lut(points)
    for line in rep.summary_lines():
        print("  " + line)

    # 断言
    assert rep.raw_monotonic == expect_raw_monotonic, "单调性判定与预期不符"
    assert rep.final_monotonic, "最终 LUT 必须单调不减"
    assert rep.lut_inversions_after == 0
    assert rep.endpoints_ok == expect_endpoints_ok
    assert lut[0] == 0 and lut[255] == 255

    # 对拍: 随机图 + 全灰阶图, LUT 查表 vs 逐点插值
    rng = random.Random(20261005)
    img = bytes(rng.randrange(256) for _ in range(64 * 64 * 3))
    img += bytes(range(256))     # 覆盖全部 256 个灰阶
    fast = apply_lut(lut, img)
    ref = map_pointwise(points, img)
    diffs = [abs(a - b) for a, b in zip(fast, ref)]
    max_diff = max(diffs)
    n_diff = sum(1 for d in diffs if d)
    print("  对拍: 像素数=%d, 不一致像素=%d, 最大绝对差=%d (仅允许四舍五入差1)"
          % (len(img), n_diff, max_diff))
    assert max_diff <= 1, "LUT 与逐点插值差异超过量化误差"
    print("  结果: PASS")
    print()
    return lut, rep, max_diff, n_diff


def benchmark(lut):
    print("=" * 66)
    print("性能测试: %d 张 %dx%dx%d 图, apply_lut 查表映射" % (N_IMAGES, W, H, C))
    rng = random.Random(7)
    imgs = [rng.randbytes(W * H * C) for _ in range(N_IMAGES)]

    tracemalloc.start()
    t0 = time.perf_counter()
    total = 0
    for img in imgs:
        out = apply_lut(lut, img)
        total += out[0]          # 防止结果被优化掉, 同时校验有输出
    t1 = time.perf_counter()
    _, py_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    elapsed = t1 - t0
    rss_peak_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print("  总像素: %d (%.1f MP)" % (N_IMAGES * W * H * C, N_IMAGES * W * H * C / 1e6))
    print("  总耗时: %.3f s" % elapsed)
    print("  单张均值: %.3f ms" % (elapsed / N_IMAGES * 1000))
    print("  吞吐: %.1f 张/s, %.1f MP/s" % (N_IMAGES / elapsed, N_IMAGES * W * H * C / 1e6 / elapsed))
    print("  Python 堆内存峰值(tracemalloc): %.2f MB" % (py_peak / 1e6))
    print("  进程 RSS 峰值(ru_maxrss, 含 1 万张输入图常驻): %.2f MB" % (rss_peak_kb / 1024))
    print()


def main():
    print("Python %s" % sys.version.split()[0])
    print()

    # 情形1: 线性曲线（恒等）
    check_case("线性曲线(恒等)", [(0, 0), (255, 255)], expect_raw_monotonic=True)

    # 情形2: 强对比 S 曲线
    check_case("强对比S曲线", [(0, 0), (64, 16), (128, 128), (192, 240), (255, 255)],
               expect_raw_monotonic=True)

    # 情形3: 控制点导致非单调（中间下凹，肉眼难察觉的反转）
    check_case("非单调控制点(需修正)", [(0, 0), (64, 120), (128, 90), (192, 200), (255, 255)],
               expect_raw_monotonic=False)

    # 情形4: 端点越界（零点映到 40、满值映到 300）
    check_case("端点越界(需钉住端点)", [(0, 40), (128, 128), (255, 300)],
               expect_raw_monotonic=True)

    # 情形5: 内部越界 + 非单调组合
    check_case("内部越界+非单调组合", [(0, 0), (60, 280), (128, 50), (200, 260), (255, 255)],
               expect_raw_monotonic=False)

    # 性能: 用强对比曲线的 LUT 跑 1 万张图
    lut, _ = build_lut([(0, 0), (64, 16), (128, 128), (192, 240), (255, 255)])
    benchmark(lut)
    print("全部自测通过。")


if __name__ == "__main__":
    main()
