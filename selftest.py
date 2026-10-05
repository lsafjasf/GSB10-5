"""heightmap 库自测：正确性校验、角度误差、缺失处理、性能。

运行：python3 selftest.py
"""

import math
import resource
import time
import tracemalloc
from array import array

from heightmap import (HeightmapError, angle_errors, compute_normals,
                       downsample, fill_missing, interpolate_normals,
                       upsample_bilinear, validate_normals)

FAILURES = []


def check(name, ok, detail=""):
    tag = "PASS" if ok else "FAIL"
    if not ok:
        FAILURES.append(name)
    print("  [%s] %s%s" % (tag, name, ("  " + detail) if detail else ""))


def make_grid(width, height, fn):
    grid = array('f', bytes(4 * width * height))
    for y in range(height):
        base = y * width
        for x in range(width):
            grid[base + x] = fn(x, y)
    return grid


def report_normals(name, normals):
    max_dev, min_nz = validate_normals(normals)
    check("%s: 全部 %d 条法线归一" % (name, len(normals) // 3),
          max_dev < 1e-5, "max |len-1| = %.3e" % max_dev)
    check("%s: 全部法线指向 +z 半球" % name, min_nz > 0.0,
          "min n_z = %.6f" % min_nz)
    return max_dev, min_nz


# ---------------------------------------------------------------- 地形生成
def terrain_flat(x, y):
    return 0.0


def terrain_slope(x, y):
    return 0.3 * x + 0.1 * y


def terrain_wavy(x, y):
    return 5.0 * math.sin(x * 0.05) * math.cos(y * 0.05)


def terrain_cliff(x, y):
    return 50.0 if x >= 512 else 0.0


def terrain_mixed(x, y):
    base = 0.05 * x + 0.02 * y + 3.0 * math.sin(x * 0.08) * math.cos(y * 0.06)
    return base + (40.0 if x >= 512 else 0.0)


# ---------------------------------------------------------------- 场景 1/2
def test_flat_and_slope():
    print("== 场景 1: 平坦地形 ==")
    w = h = 129
    grid = make_grid(w, h, terrain_flat)
    up, uw, uh = upsample_bilinear(w, h, grid, 4)
    check("平坦地形插值后仍全为 0", all(v == 0.0 for v in up))
    normals = compute_normals(uw, uh, up, 0.25)
    report_normals("平坦", normals)
    exact = all(normals[3 * i] == 0.0 and normals[3 * i + 1] == 0.0
                and normals[3 * i + 2] == 1.0 for i in range(uw * uh))
    check("平坦地形法线精确等于 (0,0,1)", exact)

    print("== 场景 2: 缓坡（平面） ==")
    grid = make_grid(w, h, terrain_slope)
    up, uw, uh = upsample_bilinear(w, h, grid, 4)
    normals = compute_normals(uw, uh, up, 0.25)
    report_normals("缓坡", normals)
    # 平面解析法线
    nx, ny, nz = -0.3, -0.1, 1.0
    inv = 1.0 / math.sqrt(nx * nx + ny * ny + nz * nz)
    ref = array('f', bytes(12 * uw * uh))
    for i in range(uw * uh):
        ref[3 * i] = nx * inv
        ref[3 * i + 1] = ny * inv
        ref[3 * i + 2] = nz * inv
    mean, worst, _, _ = angle_errors(ref, normals)
    # 高度以 float32 存储，量化误差经差分放大后残差约 1e-2 度量级
    check("缓坡法线与解析解一致（float32 量化残差内）", worst < 2e-2,
          "mean=%.2e deg, max=%.2e deg" % (mean, worst))


# ---------------------------------------------------------------- 场景 3
def test_cliff_and_resolution():
    print("== 场景 3: 悬崖 —— 逐点校验 + 角度误差随分辨率变化 ==")
    n = 1025
    fine = make_grid(n, n, terrain_cliff)
    ref_normals = compute_normals(n, n, fine, 1.0)
    report_normals("悬崖(参考 1025x1025)", ref_normals)

    print("  分辨率对比（参考 = 1025x1025 高分辨率网格法线）:")
    print("  %-14s %12s %12s %s" % ("插值倍数", "平均误差(°)", "最大误差(°)", "最大误差位置"))
    for factor in (2, 4, 8, 16):
        coarse, cw, ch = downsample(n, n, fine, factor)
        up, uw, uh = upsample_bilinear(cw, ch, coarse, factor)
        # 粗网格间距为 factor，插值放大后间距回到 1.0
        normals = compute_normals(uw, uh, up, 1.0)
        mean, worst, worst_i, _ = angle_errors(ref_normals, normals)
        wx, wy = worst_i % uw, worst_i // uw
        print("  %-14d %12.4f %12.4f   (%d,%d)" % (factor, mean, worst, wx, wy))
        max_dev, min_nz = validate_normals(normals)
        check("悬崖 factor=%d 法线归一且朝上" % factor,
              max_dev < 1e-5 and min_nz > 0.0,
              "max|len-1|=%.2e min_nz=%.4f" % (max_dev, min_nz))


# ---------------------------------------------------------------- 场景 3b
def test_order_comparison():
    print("== 场景 3b: 顺序对比 —— 先插值高度再算法线 vs 先算法线再插值法线 ==")
    n = 1025
    fine = make_grid(n, n, terrain_mixed)
    ref_normals = compute_normals(n, n, fine, 1.0)
    print("  %-10s | %-28s | %-28s" % ("factor", "A: 插值高度->法线", "B: 法线->插值法线"))
    print("  %-10s | %12s %13s | %12s %13s" % ("", "mean(°)", "max(°)", "mean(°)", "max(°)"))
    for factor in (4, 8, 16):
        coarse, cw, ch = downsample(n, n, fine, factor)
        up, uw, uh = upsample_bilinear(cw, ch, coarse, factor)
        na = compute_normals(uw, uh, up, 1.0)
        mean_a, max_a, _, _ = angle_errors(ref_normals, na)
        coarse_n = compute_normals(cw, ch, coarse, float(factor))
        nb, nw, nh = interpolate_normals(cw, ch, coarse_n, factor)
        mean_b, max_b, _, _ = angle_errors(ref_normals, nb)
        print("  %-10d | %12.4f %13.4f | %12.4f %13.4f"
              % (factor, mean_a, max_a, mean_b, max_b))


# ---------------------------------------------------------------- 场景 4
def test_missing():
    print("== 场景 4: 含缺失高度 ==")
    w = h = 257
    grid = make_grid(w, h, terrain_wavy)
    # 人为制造缺失：一个 20x20 方块、一条竖缝、一批散点
    holes = 0
    for y in range(100, 120):
        for x in range(60, 80):
            grid[y * w + x] = float('nan')
            holes += 1
    for y in range(0, h):
        grid[y * w + 200] = float('nan')
        holes += 1
    for y in range(h):
        for x in range(w):
            if (x * 7 + y * 13) % 211 == 0 and grid[y * w + x] == grid[y * w + x]:
                grid[y * w + x] = float('nan')
                holes += 1
    print("  人工缺失格点数: %d / %d" % (holes, w * h))

    filled, count = fill_missing(w, h, grid)
    check("缺失全部填补", count == holes and not any(
        math.isnan(v) for v in filled), "填补 %d 点" % count)

    filled2, count2 = fill_missing(w, h, grid)
    check("填补规则确定性（两次结果逐点一致）",
          count2 == count and filled2 == filled)

    # 填补值合理性：填补值应落在其有效邻域的 [min, max] 内（均值性质）
    ok = True
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            i = y * w + x
            if math.isnan(grid[i]):
                nb = [filled[i - 1], filled[i + 1], filled[i - w], filled[i + w]]
                if not (min(nb) - 1e-4 <= filled[i] <= max(nb) + 1e-4):
                    ok = False
    check("填补值位于邻域均值包络内", ok)

    normals = compute_normals(w, h, filled, 1.0)
    report_normals("缺失填补后", normals)

    try:
        fill_missing(4, 4, array('f', [float('nan')] * 16))
        check("全缺失拒绝", False, "未抛出异常")
    except HeightmapError as exc:
        check("全缺失拒绝", True, str(exc))


# ---------------------------------------------------------------- 性能
def test_performance():
    print("== 性能: 100 万格点 ==")
    w = h = 1000  # 1,000,000 格点
    grid = make_grid(w, h, lambda x, y: 0.01 * x + 0.02 * y
                     + 2.0 * math.sin(x * 0.05) * math.cos(y * 0.04))
    # 制造 5% 缺失
    for i in range(0, w * h, 20):
        grid[i] = float('nan')

    tracemalloc.start()
    t0 = time.perf_counter()
    filled, filled_count = fill_missing(w, h, grid)
    t1 = time.perf_counter()
    normals = compute_normals(w, h, filled, 1.0)
    t2 = time.perf_counter()
    coarse, cw, ch = downsample(w, h, filled, 4)
    up, uw, uh = upsample_bilinear(cw, ch, coarse, 4)
    t3 = time.perf_counter()
    up_normals = compute_normals(uw, uh, up, 0.25)
    t4 = time.perf_counter()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0

    print("  格点数                 : %d x %d = %d" % (w, h, w * h))
    print("  缺失填补 (%d 点)   : %.3f s" % (filled_count, t1 - t0))
    print("  法线计算 (100 万点)    : %.3f s" % (t2 - t1))
    print("  4x 双线性插值 -> %d 点 : %.3f s" % (uw * uh, t3 - t2))
    print("  插值网格法线           : %.3f s" % (t4 - t3))
    print("  Python 堆内存峰值      : %.1f MB (tracemalloc)" % (peak / 1e6))
    print("  进程常驻内存峰值(RSS)  : %.1f MB (ru_maxrss)" % rss_mb)
    max_dev, min_nz = validate_normals(normals)
    check("性能测试法线归一且朝上", max_dev < 1e-5 and min_nz > 0.0)
    max_dev, min_nz = validate_normals(up_normals)
    check("插值网格法线归一且朝上", max_dev < 1e-5 and min_nz > 0.0)


def main():
    test_flat_and_slope()
    test_cliff_and_resolution()
    test_order_comparison()
    test_missing()
    test_performance()
    print()
    if FAILURES:
        print("FAILED: %d 项未通过: %s" % (len(FAILURES), FAILURES))
        raise SystemExit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
