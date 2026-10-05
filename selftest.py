# -*- coding: utf-8 -*-
"""
selftest.py — depth_backprojection 的自测 / 对拍 / 基准脚本。

覆盖：
  A. 无畸变相机：已知三维点投影->量化->反投影对拍，空间误差统计
  B. 强畸变相机：有/无畸变补偿对照（整体 + 视场边缘）
  C. 缺失深度（0 值）：跳过 + 标记行为验证
  D. 去畸变迭代收敛性验证
  E. 100 万像素（1280x800）反投影耗时与内存峰值

运行：python3 selftest.py
"""

import math
import random
import resource
import time
import tracemalloc
from array import array

from depth_backprojection import (
    Intrinsics, BrownConradyDistortion,
    project_point, pixel_index,
    backproject_pixel, backproject_depth,
)

WIDTH, HEIGHT = 1280, 800
INTR = Intrinsics(fx=600.0, fy=600.0, cx=639.5, cy=399.5)
DIST_NONE = BrownConradyDistortion()
DIST_STRONG = BrownConradyDistortion(k1=-0.2, k2=0.05,
                                     p1=0.001, p2=-0.001,
                                     k3=0.005)


def make_scene_points(seed=42):
    """
    在归一化像面上均匀铺点（覆盖到视场四角之外一点），
    深度随机 1.0~5.0 m，投影落在图内的保留。
    返回 [(X,Y,Z,i,j,z_mm), ...]
    """
    rng = random.Random(seed)
    pts = []
    nx, ny = 40, 25
    for gy in range(ny):
        for gx in range(nx):
            x = -1.15 + 2.30 * gx / (nx - 1)
            y = -0.75 + 1.50 * gy / (ny - 1)
            Z = rng.uniform(1.0, 5.0)
            pts.append((x * Z, y * Z, Z))
    return pts


def render(points, dist):
    """投影 + 取整 + 深度毫米量化；返回落在图内的样本列表。"""
    samples = []
    for (X, Y, Z) in points:
        uv = project_point((X, Y, Z), INTR, dist)
        if uv is None:
            continue
        i, j = pixel_index(*uv)
        if 0 <= i < WIDTH and 0 <= j < HEIGHT:
            z_mm = int(math.floor(Z * 1000.0 + 0.5))
            samples.append((X, Y, Z, i, j, z_mm))
    return samples


def err_stats(samples, dist_used, edge_only=False):
    """对拍：逐点反投影并与真值比较，返回 (n, mean, max, p95) 单位 mm。"""
    errs = []
    corner_r = math.hypot(INTR.cx, INTR.cy)
    for (X, Y, Z, i, j, z_mm) in samples:
        if edge_only:
            r = math.hypot(i - INTR.cx, j - INTR.cy)
            if r < 0.8 * corner_r:
                continue
        Xb, Yb, Zb, ok = backproject_pixel(i, j, z_mm, INTR, dist_used)
        assert ok
        e = math.sqrt((Xb - X) ** 2 + (Yb - Y) ** 2 + (Zb - Z) ** 2)
        errs.append(e * 1000.0)  # m -> mm
    errs.sort()
    n = len(errs)
    if n == 0:
        return 0, float('nan'), float('nan'), float('nan')
    mean = sum(errs) / n
    p95 = errs[int(0.95 * (n - 1))]
    return n, mean, errs[-1], p95


def fmt(stats):
    n, mean, mx, p95 = stats
    return "n=%4d  mean=%7.3f mm  p95=%7.3f mm  max=%8.3f mm" \
           % (n, mean, p95, mx)


def scenario_A(points):
    print("=" * 72)
    print("场景 A：无畸变相机，投影->量化->反投影 对拍")
    print("=" * 72)
    samples = render(points, DIST_NONE)
    print("  有效采样点: %d" % len(samples))
    print("  全图      : " + fmt(err_stats(samples, DIST_NONE)))
    print("  视场边缘  : " + fmt(err_stats(samples, DIST_NONE, True)))
    return samples


def scenario_B(points):
    print()
    print("=" * 72)
    print("场景 B：强畸变相机 (k1=-0.2,k2=0.05,p1=0.001,p2=-0.001,k3=0.005)")
    print("        有/无畸变补偿对照")
    print("=" * 72)
    samples = render(points, DIST_STRONG)
    print("  有效采样点: %d" % len(samples))
    print("  [有补偿] 全图    : " + fmt(err_stats(samples, DIST_STRONG)))
    print("  [有补偿] 视场边缘: " + fmt(err_stats(samples, DIST_STRONG, True)))
    print("  [无补偿] 全图    : " + fmt(err_stats(samples, None)))
    print("  [无补偿] 视场边缘: " + fmt(err_stats(samples, None, True)))

    # 边缘像素畸变位移量（像素）直观展示
    i, j = WIDTH - 1, HEIGHT - 1
    xd, yd = (i - INTR.cx) / INTR.fx, (j - INTR.cy) / INTR.fy
    x, y = DIST_STRONG.undistort_normalized(xd, yd)
    du = INTR.fx * (x - xd)
    dv = INTR.fy * (y - yd)
    print("  右下角像素 (%d,%d) 的畸变位移: %.2f px (du=%.2f, dv=%.2f)"
          % (i, j, math.hypot(du, dv), du, dv))
    return samples


def scenario_C(samples):
    print()
    print("=" * 72)
    print("场景 C：缺失深度（0 值）处理")
    print("=" * 72)
    # 稠密深度图（倾斜平面 1.5~3.5 m），再随机挖 5% 空洞模拟缺失
    total = WIDTH * HEIGHT
    depth = array('H', b'\x00\x00') * total
    for j in range(HEIGHT):
        base = j * WIDTH
        for i in range(WIDTH):
            depth[base + i] = 1500 + (2000 * (i + j)) // (WIDTH + HEIGHT)
    rng = random.Random(7)
    holes = set(rng.sample(range(total), total // 20))
    for idx in holes:
        depth[idx] = 0

    xs, ys, zs, valid, count = backproject_depth(depth, WIDTH, HEIGHT,
                                                 INTR, DIST_STRONG)
    nonzero = total - len(holes)
    print("  图像尺寸        : %d x %d = %d px" % (WIDTH, HEIGHT, total))
    print("  空洞(零深度)    : %d (5%%)" % len(holes))
    print("  非零深度像素    : %d" % nonzero)
    print("  valid 掩码计数  : %d  (应等于非零深度像素数)" % count)
    assert count == nonzero, "valid 计数与非零深度像素不一致!"

    # 抽查：空洞位置坐标为 (0,0,0) 且 valid=0；有效位置 Z 与深度一致
    bad = 0
    for idx in list(holes)[:2000]:
        if valid[idx] != 0 or xs[idx] != 0.0 or ys[idx] != 0.0 \
                or zs[idx] != 0.0:
            bad += 1
    ok_z = 0
    checked = 0
    for idx in range(0, total, 997):
        if idx not in holes:
            checked += 1
            if abs(zs[idx] - depth[idx] * 0.001) < 1e-4 and valid[idx] == 1:
                ok_z += 1
    print("  空洞抽查(2000处): 坐标=(0,0,0) 且 valid=0 -> %s"
          % ("全部符合" if bad == 0 else "%d 处异常!" % bad))
    print("  有效抽查(%d处) : Z 值与深度图一致 -> %d 处符合" % (checked, ok_z))
    assert ok_z == checked
    r = backproject_pixel(100, 100, 0, INTR, DIST_STRONG)
    print("  单像素接口 z=0  : 返回 valid=%s, 坐标=(%.1f,%.1f,%.1f)"
          % (r[3], r[0], r[1], r[2]))
    assert bad == 0 and r[3] is False
    print("  结论: 缺失/零深度像素被确定性地跳过并标记，不产生伪点。")


def scenario_D():
    print()
    print("=" * 72)
    print("场景 D：去畸变不动点迭代收敛性（强畸变，12 次迭代）")
    print("=" * 72)
    worst = 0.0
    for j in range(0, HEIGHT, 40):
        for i in range(0, WIDTH, 40):
            xd = (i - INTR.cx) / INTR.fx
            yd = (j - INTR.cy) / INTR.fy
            x, y = DIST_STRONG.undistort_normalized(xd, yd)
            xd2, yd2 = DIST_STRONG.distort_normalized(x, y)
            res = math.hypot(xd2 - xd, yd2 - yd)
            worst = max(worst, res)
    print("  往返残差 max |distort(undistort(p)) - p| = %.3e (归一化坐标)"
          % worst)
    print("  折合像素: %.3e px" % (worst * INTR.fx))
    assert worst < 1e-5


def scenario_E():
    print()
    print("=" * 72)
    print("场景 E：100 万像素 (1280x800=%d) 反投影耗时与内存峰值"
          % (WIDTH * HEIGHT))
    print("=" * 72)
    rng = random.Random(1)
    total = WIDTH * HEIGHT
    depth = array('H', (0 if rng.random() < 0.02
                        else rng.randint(500, 5000)
                        for _ in range(total)))

    for name, dist in (("无畸变  ", DIST_NONE), ("强畸变  ", DIST_STRONG)):
        tracemalloc.start()
        t0 = time.perf_counter()
        xs, ys, zs, valid, count = backproject_depth(
            depth, WIDTH, HEIGHT, INTR, dist)
        dt = time.perf_counter() - t0
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
        print("  [%s] 耗时 %6.2f s (%5.2f us/px)  "
              "tracemalloc 峰值 %6.2f MiB  进程峰值 RSS %6.1f MiB  "
              "有效点 %d"
              % (name, dt, dt / total * 1e6, peak / 1048576.0, rss, count))
        del xs, ys, zs, valid


def main():
    print("相机: %dx%d, fx=fy=600, cx=639.5, cy=399.5" % (WIDTH, HEIGHT))
    print("深度量化: uint16 毫米, 0=缺失; 取整: 像素四舍五入, 深度最近毫米")
    points = make_scene_points()
    samples = scenario_A(points)
    scenario_B(points)
    scenario_C(samples)
    scenario_D()
    scenario_E()
    print()
    print("全部断言通过。")


if __name__ == "__main__":
    main()
