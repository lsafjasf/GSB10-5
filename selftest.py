"""色调曲线自测：单调性校验、端点边界、逐点对拍、1 万张图耗时/内存基准。

运行：python3 selftest.py
"""

from __future__ import annotations

import random
import resource
import sys
import time
import tracemalloc
import zlib

from tonecurve import ToneCurve, evaluate_curve, _check_pairs, _check_lut

# ---------------------------------------------------------------- 场景定义

SCENARIOS = [
    (
        "1. 线性曲线（恒等映射）",
        [(0.0, 0.0), (1.0, 1.0)],
    ),
    (
        "2. 强对比 S 曲线",
        [(0.0, 0.0), (0.25, 0.08), (0.5, 0.5), (0.75, 0.92), (1.0, 1.0)],
    ),
    (
        "3. 控制点导致非单调（需修正）",
        [(0.0, 0.0), (0.3, 0.8), (0.6, 0.2), (1.0, 1.0)],
    ),
    (
        "4. 端点越界（零点/满值被强制拉回）",
        [(0.0, 0.35), (0.5, 0.5), (1.0, 0.72)],
    ),
]

# 基准曲线：强对比 S 曲线
BENCH_POINTS = SCENARIOS[1][1]

# ---------------------------------------------------------------- 工具函数


def make_image(width: int, height: int, seed: int) -> bytes:
    """确定性伪随机灰度图。"""
    rng = random.Random(seed)
    return rng.randbytes(width * height)


def reference_map_pointwise(curve: ToneCurve, image: bytes) -> bytes:
    """参考实现：逐像素在最终控制点曲线上做分段线性插值（不查 LUT）。"""
    pts = curve.points
    out = bytearray(len(image))
    for i, p in enumerate(image):
        y = evaluate_curve(pts, p / 255.0)
        out[i] = int(min(1.0, max(0.0, y)) * 255.0 + 0.5)
    return bytes(out)


def reference_map_lut(curve: ToneCurve, image: bytes) -> bytes:
    """参考实现：逐像素查 256 级 LUT（Python 循环，非 translate）。"""
    lut = curve.lut
    out = bytearray(len(image))
    for i, p in enumerate(image):
        out[i] = lut[p]
    return bytes(out)


def check_monotonic_detail(curve: ToneCurve) -> tuple[int, int, int, int]:
    """逐一校验：返回 (控制点对数, 违例数, LUT 对数, LUT 违例数)。"""
    cp_viol = _check_pairs(curve.points)
    lut_viol = _check_lut(curve.lut)
    return len(curve.points) - 1, len(cp_viol), 255, len(lut_viol)


def print_report(name: str, curve: ToneCurve) -> None:
    r = curve.report
    print(f"{'=' * 66}")
    print(f"场景：{name}")
    print(f"{'=' * 66}")
    print(f"原始控制点: {[(round(x, 4), round(y, 4)) for x, y in r.raw_points]}")
    if r.duplicate_x_merged:
        print(f"重复 x 合并: {r.duplicate_x_merged}")
    if r.y_clamped:
        print(f"y 越界夹取: {r.y_clamped}")
    if r.endpoint_actions:
        for a in r.endpoint_actions:
            print(f"端点处理: {a}")
    else:
        print("端点处理: 原始端点已满足 (0->0, 1->1)，无需处理")
    print(f"最终控制点: {[(round(x, 4), round(y, 4)) for x, y in r.final_points]}")

    print("--- 单调性逐一校验（控制点，修正前） ---")
    if r.cp_violations_before:
        for v in r.cp_violations_before:
            print(f"  违例 {v.describe()}")
        print(f"  结果: {len(r.cp_violations_before)} 处违例 -> 保序回归(PAVA)修正")
    else:
        n = len(r.prepared_points) - 1
        print(f"  {n} 对相邻控制点全部非递减，通过")

    print("--- 单调性逐一校验（控制点，修正后） ---")
    n_pairs = len(r.final_points) - 1
    if r.cp_violations_after:
        for v in r.cp_violations_after:
            print(f"  违例 {v.describe()}")
    else:
        print(f"  {n_pairs} 对相邻控制点全部非递减，通过")

    print("--- 单调性逐一校验（LUT 256 级，255 对相邻项） ---")
    if r.lut_violations:
        for v in r.lut_violations:
            print(f"  违例 {v.describe()}")
    else:
        print("  255 对相邻项全部非递减，通过")
    print(f"  单调性结论: {'单调（非递减）' if curve.report.is_monotonic else '非单调！'}")

    y0, y1, l0, l255 = r.endpoint_result
    print("--- 端点边界 ---")
    print(f"  曲线端点: f(0.0)={y0} (要求 0.0), f(1.0)={y1} (要求 1.0)")
    print(f"  LUT 端点: LUT[0]={l0} (要求 0), LUT[255]={l255} (要求 255)")
    ok = (y0 == 0.0 and y1 == 1.0 and l0 == 0 and l255 == 255)
    print(f"  端点结论: {'通过' if ok else '失败'}")


def cross_check(name: str, curve: ToneCurve) -> None:
    """对拍：translate 快路径 vs 逐点查 LUT vs 逐点曲线插值。"""
    print("--- 对拍（translate vs 逐点查表 vs 逐点曲线插值） ---")
    sizes = [(64, 64), (257, 131), (512, 512)]
    total_pixels = 0
    total_mismatch_lut = 0
    total_mismatch_curve = 0
    for idx, (w, h) in enumerate(sizes):
        img = make_image(w, h, seed=1000 + idx)
        fast = curve.apply(img)
        ref_lut = reference_map_lut(curve, img)
        ref_curve = reference_map_pointwise(curve, img)
        mm_lut = sum(1 for a, b in zip(fast, ref_lut) if a != b)
        mm_curve = sum(1 for a, b in zip(fast, ref_curve) if a != b)
        total_pixels += len(img)
        total_mismatch_lut += mm_lut
        total_mismatch_curve += mm_curve
        print(
            f"  图 {w}x{h} ({len(img)} 像素): "
            f"vs 逐点查表 不一致={mm_lut}, vs 逐点插值 不一致={mm_curve}"
        )
    print(
        f"  合计: {total_pixels} 像素, 查表对拍不一致={total_mismatch_lut}, "
        f"插值对拍不一致={total_mismatch_curve}"
    )
    assert total_mismatch_lut == 0, "translate 与逐点查表不一致！"
    assert total_mismatch_curve == 0, "translate 与逐点曲线插值不一致！"


# ---------------------------------------------------------------- 基准测试


def bench_timing(curve: ToneCurve, n: int, w: int, h: int) -> None:
    print(f"{'=' * 66}")
    print(f"基准：{n} 张 {w}x{h} 灰度图（仅计映射耗时，不含图像生成）")
    print(f"{'=' * 66}")
    print("  说明: 映射耗时与图像内容无关（纯 LUT 查表），故复用同一幅确定性"
          "伪随机图循环 n 次，避免把图像生成/IO 计入映射耗时。")
    rng = random.Random(2026)
    tile = rng.randbytes(w * h)
    checksum = 0
    t0 = time.perf_counter()
    for _ in range(n):
        out = curve.apply(tile)
        checksum = zlib.crc32(out, checksum)
    dt = time.perf_counter() - t0
    total_px = n * w * h
    print(f"  总像素: {total_px:,} ({total_px / 1e6:.1f} MP)")
    print(f"  总耗时: {dt:.3f} s")
    print(f"  平均每张: {dt / n * 1e3:.4f} ms")
    print(f"  吞吐: {total_px / dt / 1e6:.1f} MP/s")
    print(f"  输出校验和 crc32: {checksum:#010x}（防止结果被优化掉）")


def bench_memory(curve: ToneCurve, n: int, w: int, h: int) -> None:
    print(f"{'=' * 66}")
    print(f"基准：{n} 张 {w}x{h} 灰度图内存峰值（逐张映射并丢弃）")
    print(f"{'=' * 66}")
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    tracemalloc.start()
    rng = random.Random(2026)
    tile = rng.randbytes(w * h)
    checksum = 0
    for _ in range(n):
        out = curve.apply(tile)
        checksum = zlib.crc32(out, checksum)
        del out
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(f"  tracemalloc Python 堆峰值: {peak / 1024 / 1024:.2f} MiB")
    print(f"  进程 RSS 峰值(ru_maxrss): {rss_after / 1024:.2f} MiB "
          f"(本轮新增 {(rss_after - rss_before) / 1024:.2f} MiB)")
    print(f"  说明: 单张图 {w * h / 1024 / 1024:.2f} MiB，逐张处理、不驻留，"
          f"峰值≈1 张输入 + 1 张输出")
    print(f"  输出校验和 crc32: {checksum:#010x}")


# ---------------------------------------------------------------- 主流程


def main() -> None:
    curves: list[tuple[str, ToneCurve]] = []
    for name, pts in SCENARIOS:
        curve = ToneCurve(pts)
        curves.append((name, curve))
        print_report(name, curve)
        cross_check(name, curve)
        print()

    # 硬性断言：全部场景最终必须单调、端点必须达标
    for name, curve in curves:
        r = curve.report
        assert r.is_monotonic, f"{name} 修正后仍非单调"
        y0, y1, l0, l255 = r.endpoint_result
        assert (y0, y1, l0, l255) == (0.0, 1.0, 0, 255), f"{name} 端点不达标"
    # 场景 3 必须真的触发过非单调修正，场景 4 必须真的触发过端点强制
    assert curves[2][1].report.cp_corrected, "场景3 未触发非单调修正"
    assert curves[3][1].report.endpoint_actions, "场景4 未触发端点强制"
    print("全部断言通过：4 个场景均单调、端点达标、对拍零误差。")
    print()

    bench_curve = ToneCurve(BENCH_POINTS)
    bench_timing(bench_curve, n=10_000, w=512, h=512)
    print()
    bench_memory(bench_curve, n=10_000, w=512, h=512)


if __name__ == "__main__":
    sys.exit(main())
