"""逐步对拍：slerp.py（球面坐标法） vs reference.py（笛卡尔 slerp）。

对每组端点，在 t = 0, 0.05, ..., 1.0 共 21 步上比较两者输出，
误差用圆心角（度）衡量，输出每组的最大/平均误差及总体统计。
"""

import math
import random

from slerp import slerp
from reference import ref_slerp, angular_error

STEPS = 21

STRUCTURED_CASES = [
    ("同半球短弧", (35.0, 120.0), (40.0, 130.0)),
    ("跨经度分界", (10.0, 170.0), (10.0, -170.0)),
    ("跨分界+跨赤道", (-20.0, 179.9), (30.0, -179.9)),
    ("极点附近(高纬跨分界)", (80.0, -170.0), (80.0, 170.0)),
    ("极点附近(过极点)", (89.0, 0.0), (89.0, 180.0)),
    ("起点即北极点", (90.0, 0.0), (0.0, 180.0)),
    ("对跖点(赤道)", (0.0, 0.0), (0.0, 180.0)),
    ("对跖点(一般)", (10.0, 30.0), (-10.0, -150.0)),
    ("两点重合", (25.0, 60.0), (25.0, 60.0)),
    ("长弧(近对跖)", (-40.0, -100.0), (45.0, 79.0)),
]


def compare_pair(p1, p2):
    worst = 0.0
    total = 0.0
    for i in range(STEPS):
        t = i / (STEPS - 1)
        err = math.degrees(angular_error(slerp(*p1, *p2, t), ref_slerp(*p1, *p2, t)))
        worst = max(worst, err)
        total += err
    return worst, total / STEPS


def main():
    print("== 结构化用例（误差单位：度）==")
    print(f"{'用例':<18} {'最大误差':>14} {'平均误差':>14}")
    gmax = 0.0
    for name, p1, p2 in STRUCTURED_CASES:
        worst, mean = compare_pair(p1, p2)
        gmax = max(gmax, worst)
        print(f"{name:<18} {worst:>14.3e} {mean:>14.3e}")

    rng = random.Random(20261005)
    n_pairs = 20000
    worst_all = 0.0
    total_all = 0.0
    count = 0
    for _ in range(n_pairs):
        p1 = (rng.uniform(-90, 90), rng.uniform(-180, 180))
        p2 = (rng.uniform(-90, 90), rng.uniform(-180, 180))
        worst, mean = compare_pair(p1, p2)
        worst_all = max(worst_all, worst)
        total_all += mean
        count += 1
    print(f"\n== 随机对拍：{n_pairs} 对端点 x {STEPS} 步 =="
          f"（共 {n_pairs * STEPS} 次插值）")
    print(f"最大误差: {worst_all:.3e} 度")
    print(f"平均误差: {total_all / count:.3e} 度")
    print(f"结构化用例最大误差: {gmax:.3e} 度")


if __name__ == "__main__":
    main()
