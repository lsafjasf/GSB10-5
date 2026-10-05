"""verify.py — 对拍验证 + 事件序列演示。运行：python3 verify.py

对拍方式：
  1) 解析解：构造已知精确 TOI 的用例，直接比对。
  2) 极小步长参考解：把一帧细分为 4096 步扫描 g(t)=dist²-R² 的符号变化，
     再二分 80 次求精，作为与实现无关的参考值（随机 2000 例 + 定点用例）。
"""

import math
import random

from ccd import COLLIDE, TOUCH, Box, Circle, simulate, sweep_circle_circle


def reference_toi(a, b, dt, steps=4096):
    """与库实现无关的参考解：细步扫描 + 二分求 g(t)=0 的首个根。"""
    rx = a.x - b.x
    ry = a.y - b.y
    ux = (a.vx - b.vx) * dt
    uy = (a.vy - b.vy) * dt
    r2 = (a.r + b.r) ** 2

    def g(t):
        dx = rx + ux * t
        dy = ry + uy * t
        return dx * dx + dy * dy - r2

    if g(0.0) <= 0.0:
        return 0.0
    prev_t, prev_g = 0.0, g(0.0)
    for k in range(1, steps + 1):
        t = k / steps
        gt = g(t)
        if gt <= 0.0:
            lo, hi = prev_t, t
            for _ in range(80):
                mid = 0.5 * (lo + hi)
                if g(mid) <= 0.0:
                    hi = mid
                else:
                    lo = mid
            return 0.5 * (lo + hi)
        prev_t, prev_g = t, gt
    return None


def fmt(x):
    return "  none  " if x is None else f"{x:.12f}"


def main():
    print("=" * 74)
    print("1) 定点用例：扫掠解析 TOI vs 极小步长参考解")
    print("=" * 74)
    cases = [
        ("静止+慢速接触", Circle(0, 0, 1, 0, 1), Circle(5, 0, 0, 0, 1), 10.0),
        ("高速穿透(位移>>体长)", Circle(0, 0, 1000, 0, 0.5), Circle(100, 0, 0, 0, 0.5), 1.0),
        ("斜向擦撞", Circle(0, 0, 3, 1, 0.7), Circle(4, 2, -1, 0, 1.3), 2.0),
        ("追逐(同向差速)", Circle(0, 0, 5, 0, 1), Circle(8, 0, 1, 0, 1), 2.0),
        ("恰好相切", Circle(0, 0, 1, 0, 1), Circle(5, 2, 0, 0, 1), 10.0),
        ("平行错过", Circle(0, 0, 1, 0, 1), Circle(5, 3, 0, 0, 1), 10.0),
    ]
    print(f"{'用例':<22}{'解析TOI':>16}{'参考TOI':>16}{'绝对误差':>14}  事件")
    for name, a, b, dt in cases:
        ev = sweep_circle_circle(a, b, dt)
        ref = reference_toi(a, b, dt)
        t = ev[0] if ev else None
        err = abs(t - ref) if (t is not None and ref is not None) else float("nan")
        kind = ev[1] if ev else "-"
        print(f"{name:<22}{fmt(t):>16}{fmt(ref):>16}{err:>14.3e}  {kind}")

    print()
    print("=" * 74)
    print("2) 随机对拍：2000 例，扫掠解析 vs 参考解")
    print("=" * 74)
    rng = random.Random(42)
    n = hit = touch = miss = 0
    max_err = 0.0
    err_at = None
    for _ in range(2000):
        a = Circle(rng.uniform(-5, 5), rng.uniform(-5, 5),
                   rng.uniform(-8, 8), rng.uniform(-8, 8), rng.uniform(0.2, 1.5))
        b = Circle(rng.uniform(-5, 5), rng.uniform(-5, 5),
                   rng.uniform(-8, 8), rng.uniform(-8, 8), rng.uniform(0.2, 1.5))
        dt = rng.uniform(0.1, 3.0)
        ev = sweep_circle_circle(a, b, dt)
        ref = reference_toi(a, b, dt)
        n += 1
        if ev is None:
            if ref is not None:
                print("!! 漏检:", a, b, dt)
            miss += 1
            continue
        if ev[1] is TOUCH:
            touch += 1
            continue  # 擦边无符号变化，参考法不适用，单独验证
        hit += 1
        if ref is None:
            print("!! 误报:", a, b, dt)
            continue
        err = abs(ev[0] - ref)
        if err > max_err:
            max_err, err_at = err, ev[0]
    print(f"总例数 {n}：碰撞 {hit}，擦边 {touch}，错过 {miss}")
    print(f"碰撞例 TOI 最大绝对误差 = {max_err:.3e}（出现在 t={err_at}）")

    print()
    print("=" * 74)
    print("3) 擦边接触：相切/微偏 三类判定")
    print("=" * 74)
    for dy, expect in ((2.0, "touch"), (2.0 + 1e-7, "none"), (2.0 - 1e-7, "collide")):
        ev = sweep_circle_circle(Circle(0, 0, 1, 0, 1), Circle(5, dy, 0, 0, 1), 10.0)
        got = "none" if ev is None else ev[1]
        t = "" if ev is None else f" t={ev[0]:.6f}"
        ok = "OK" if got == expect else "MISMATCH"
        print(f"  横向偏移 {dy:.7f}  期望 {expect:<7} 实得 {got:<7}{t}  [{ok}]")

    print()
    print("=" * 74)
    print("4) 一帧内多次碰撞：事件序列（牛顿摆 + 双墙反弹）")
    print("=" * 74)
    balls = [Circle(0, 0, 1, 0, 1), Circle(3, 0, 0, 0, 1), Circle(5.5, 0, 0, 0, 1)]
    events, bodies = simulate(balls, dt=2.0)
    print("牛顿摆 A->B->C（dt=2s）：")
    for t, kind, i, j in events:
        print(f"  t={t:.6f}s  {kind:<8} 球{'ABC'[i]} <-> 球{'ABC'[j]}")
    print(f"  帧末速度: A={bodies[0].vx:.3f} B={bodies[1].vx:.3f} C={bodies[2].vx:.3f}")

    walls = [Circle(0, 0, 10, 0, 1), Box(-10, 0, 0.5, 10), Box(10, 0, 0.5, 10)]
    events, bodies = simulate(walls, dt=3.0)
    print("球在双墙间（dt=3s）：")
    names = ["球", "左墙", "右墙"]
    for t, kind, i, j in events:
        print(f"  t={t:.6f}s  {kind:<8} {names[i]} <-> {names[j]}")
    print(f"  帧末球速 vx={bodies[0].vx:.3f}")


if __name__ == "__main__":
    main()
