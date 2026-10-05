"""spherical_slerp 自测：与笛卡尔参考实现逐步对拍，并覆盖各退化情形。

运行：python3 test_spherical_slerp.py
退出码 0 表示全部断言通过；同时打印各情形的路径数据与误差数据。
"""

import math
import random
import sys

from spherical_slerp import (
    EARTH_RADIUS_KM,
    angular_error_deg,
    central_angle,
    slerp,
    slerp_cartesian,
)

TOL_DEG = 1e-9  # 对拍允许的最大角误差（度），约 0.1 微米


def fmt(lat, lon):
    return f"({lat:+12.8f}, {lon:+13.8f})"


def compare_case(name, p1, p2, steps=10):
    """对单个情形在 t=0..1 上逐步对拍，打印路径与误差，返回最大误差（度）。"""
    print(f"\n== {name} ==")
    print(f"   起点 {fmt(*p1)}  终点 {fmt(*p2)}")
    arc_deg = math.degrees(central_angle(p1[0], p1[1], p2[0], p2[1]))
    print(f"   大圆短弧长: {arc_deg:.8f} deg = "
          f"{math.radians(arc_deg) * EARTH_RADIUS_KM:.3f} km")
    print(f"   {'t':>5} | {'球面法 (lat, lon)':^41} | {'笛卡尔参考 (lat, lon)':^41} | "
          f"{'误差(deg)':>11} | {'误差(mm)':>9}")
    max_err = 0.0
    for i in range(steps + 1):
        t = i / steps
        a = slerp(p1[0], p1[1], p2[0], p2[1], t)
        b = slerp_cartesian(p1[0], p1[1], p2[0], p2[1], t)
        err = angular_error_deg(a, b)
        max_err = max(max_err, err)
        err_mm = math.radians(err) * EARTH_RADIUS_KM * 1e6
        print(f"   {t:5.2f} | {fmt(*a):^41} | {fmt(*b):^41} | {err:11.3e} | {err_mm:9.3f}")
    print(f"   本情形最大对拍误差: {max_err:.3e} deg")
    assert max_err < TOL_DEG, f"{name}: 对拍误差 {max_err} 超限"
    return max_err


def check_determinism(name, p1, p2, steps=10):
    """同一输入重复插值，结果必须逐位一致（确定性）。"""
    first = [slerp(p1[0], p1[1], p2[0], p2[1], i / steps) for i in range(steps + 1)]
    for _ in range(3):
        again = [slerp(p1[0], p1[1], p2[0], p2[1], i / steps) for i in range(steps + 1)]
        assert again == first, f"{name}: 结果不确定"
    print(f"\n== {name} 确定性检查通过（4 次运行逐位一致）==")


def main():
    failures = 0

    # 1) 同半球短弧
    compare_case("情形1: 同半球短弧 (北半球)", (30.0, 120.0), (40.0, 130.0))

    # 2) 跨越经度分界：170 -> -170，短弧 20° 而非 340°
    p1, p2 = (10.0, 170.0), (10.0, -170.0)
    compare_case("情形2: 跨越 ±180° 经度分界", p1, p2)
    arc = math.degrees(central_angle(*p1, *p2))
    # 短弧应略短于纬线弧 20°（大圆比纬线更短），且远小于绕远路的 ~340°
    assert 19.0 < arc < 20.0, f"跨分界应取短弧(~19.7°)，实际 {arc}"
    mid = slerp(*p1, *p2, 0.5)
    assert abs(abs(mid[1]) - 180.0) < 1e-9, f"中点应过 ±180°，实际 {mid}"
    lons = [slerp(*p1, *p2, i / 10)[1] for i in range(11)]
    assert all(lo > 0 for lo in lons[:5]) and abs(lons[5]) == 180.0 \
        and all(lo < 0 for lo in lons[6:]), "路径应在 180° 处换号而非绕经度一圈"
    print("   经度序列: " + " ".join(f"{lo:+.4f}" for lo in lons))
    print("   -> 经度从 +170 单调增至 +180 后跳到 -180 再增至 -170，未绕远路")

    # 3) 极点附近：同纬度 89.9°、经度差 180°，大圆应越过北极点
    p1, p2 = (89.9, 0.0), (89.9, 180.0)
    compare_case("情形3: 极点附近（大圆过北极点）", p1, p2)
    mid = slerp(*p1, *p2, 0.5)
    assert abs(mid[0] - 90.0) < 1e-9, f"中点应为北极点，实际 {mid}"
    print(f"   中点 {fmt(*mid)} -> 恰好越过北极点，结果确定")

    # 4) 对跖点：约定走过极点的大圆
    p1, p2 = (0.0, 0.0), (0.0, 180.0)
    compare_case("情形4: 对跖点（赤道上相对两点）", p1, p2)
    mid = slerp(*p1, *p2, 0.5)
    assert abs(mid[0] - 90.0) < 1e-9, f"对跖点中点应为北极点，实际 {mid}"
    end = slerp(*p1, *p2, 1.0)
    assert angular_error_deg(end, p2) < 1e-9, "t=1 必须落在终点"
    check_determinism("情形4: 对跖点", p1, p2)

    # 5) 两点重合
    p1 = p2 = (25.0, 33.0)
    compare_case("情形5: 两点重合", p1, p2)
    for i in range(11):
        r = slerp(*p1, *p2, i / 10)
        assert r == (25.0, 33.0), f"重合点插值应恒为定点，实际 {r}"
    check_determinism("情形5: 两点重合", p1, p2)

    # 6) 极点上的点（经度无定义）也要确定
    p1, p2 = (90.0, 45.0), (0.0, -120.0)
    compare_case("情形6: 起点在北极点（经度无定义）", p1, p2)
    check_determinism("情形6: 极点出发", p1, p2)

    # 7) 随机对拍：20 万对随机点
    print("\n== 情形7: 随机对拍（200,000 对 x 11 个 t 值）==")
    rng = random.Random(42)
    max_err = 0.0
    worst = None
    for _ in range(200_000):
        la1 = math.degrees(math.asin(rng.uniform(-1, 1)))
        lo1 = rng.uniform(-180, 180)
        la2 = math.degrees(math.asin(rng.uniform(-1, 1)))
        lo2 = rng.uniform(-180, 180)
        for k in range(11):
            t = k / 10
            a = slerp(la1, lo1, la2, lo2, t)
            b = slerp_cartesian(la1, lo1, la2, lo2, t)
            err = angular_error_deg(a, b)
            if err > max_err:
                max_err = err
                worst = ((la1, lo1), (la2, lo2), t)
    print(f"   最大角误差: {max_err:.3e} deg "
          f"({math.radians(max_err) * EARTH_RADIUS_KM * 1e6:.3f} mm)")
    print(f"   最差样本: p1={worst[0]}, p2={worst[1]}, t={worst[2]}")
    assert max_err < TOL_DEG, f"随机对拍误差 {max_err} 超限"

    print("\n全部断言通过。")
    return failures


if __name__ == "__main__":
    sys.exit(main())
