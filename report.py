"""生成逐帧对拍数据与累计偏移数据（python3 report.py > report.txt）。"""
from fractions import Fraction

from frc import FrameRateConverter

CASES = [
    ("整数倍率·升帧 24->48",   FrameRateConverter(24, 48, 200)),
    ("整数倍率·降帧 48->24",   FrameRateConverter(48, 24, 200)),
    ("非整数倍率·升帧 24->30", FrameRateConverter(24, 30, 200)),
    ("非整数倍率·降帧 30->24", FrameRateConverter(30, 24, 200)),
    ("NTSC 23.976->29.97",     FrameRateConverter("24000/1001", "30000/1001", 200)),
    ("首帧偏移 t0=3.5s 24->30", FrameRateConverter(24, 30, 200, t0=Fraction(7, 2))),
]


def ms(x):
    return f"{float(x) * 1000:+.4f}"


def show_case(name, c, head=8, tail=2):
    print(f"\n### {name}  (源 {c.num_src} 帧 -> 输出 {c.num_dst} 帧, "
          f"src_fps={c.src_fps}, dst_fps={c.dst_fps}, t0={c.t0})")
    print(f"{'n':>4} | {'输出时刻(精确分数)':>22} | {'输出时刻(s)':>10} | "
          f"{'目标时间轴(s)':>10} | {'对拍差(s)':>8} | {'源帧k':>4} | "
          f"{'源帧时刻(s)':>10} | {'映射误差(ms)':>11}")
    idxs = list(range(head)) + list(range(c.num_dst - tail, c.num_dst))
    for n in idxs:
        pts = c.out_pts(n)
        ideal = c.t0 + Fraction(n) / c.dst_fps
        k = c.src_index_for(n)
        print(f"{n:>4} | {str(pts):>22} | {float(pts):>10.5f} | "
              f"{float(ideal):>10.5f} | {float(pts - ideal):>8.1f} | {k:>4} | "
              f"{float(c.src_pts(k)):>10.5f} | {ms(c.mapping_error(n)):>11}")
    print(f"理论上界 |映射误差| <= 1/(2*src_fps) = "
          f"{float(Fraction(1, 2) / c.src_fps) * 1000:.4f} ms")


def show_drift():
    print("\n### 累计偏移随帧号变化（24->30，1 小时 = 86400 源帧 -> 108000 输出帧）")
    c = FrameRateConverter(24, 30, 86400)
    print("对比三种时间戳算法：")
    print("  [本库] 精确有理数，逐帧独立计算 -> 偏差恒为 0")
    print("  [毫秒取整累加] 每帧时长四舍五入到毫秒再累加（常见 bug 来源）")
    print("  [float 累加] pts += 1.0/30 逐步累加")
    print(f"{'帧号n':>8} | {'本库对拍差(s)':>12} | {'本库|映射误差|累计max(ms)':>22} | "
          f"{'毫秒取整累加漂移(s)':>16} | {'float累加漂移(s)':>14}")
    rounded_dur = Fraction(round(float(Fraction(1000, 30))), 1000)  # 33ms
    worst = Fraction(0)
    naive_pts = Fraction(0)
    float_pts = 0.0
    marks = {0, 1, 1000, 10000, 27000, 54000, 107999}
    for n in range(c.num_dst):
        e = abs(c.mapping_error(n))
        if e > worst:
            worst = e
        if n in marks:
            ideal = Fraction(n, 30)
            print(f"{n:>8} | {float(c.timeline_drift(n)):>12.1f} | "
                  f"{float(worst) * 1000:>22.4f} | "
                  f"{float(naive_pts - ideal):>16.3f} | "
                  f"{float_pts - float(ideal):>14.2e}")
        naive_pts += rounded_dur
        float_pts += 1.0 / 30.0
    print(f"片尾总帧数 {c.num_dst}；毫秒取整累加 1 小时漂 "
          f"{float(rounded_dur * c.num_dst - Fraction(c.num_dst, 30)):.3f} s，"
          f"本库恒为 0。")


def show_edges():
    print("\n### 边界情形（确定性处理）")
    for desc, c in [
        ("空素材 0 帧",            FrameRateConverter(24, 30, 0)),
        ("单帧 24->60fps",         FrameRateConverter(24, 60, 1)),
        ("单帧 60->24fps(保底1帧)", FrameRateConverter(60, 24, 1)),
        ("2帧@1fps -> 60fps",      FrameRateConverter(1, 60, 2)),
        ("t0=3.5s 首帧偏移",       FrameRateConverter(24, 30, 100, t0=Fraction(7, 2))),
    ]:
        if c.num_dst:
            print(f"  {desc:<26} 源{c.num_src}帧 -> 输出{c.num_dst}帧, "
                  f"首帧时刻={float(c.out_pts(0))}s, "
                  f"末帧时刻={float(c.out_pts(c.num_dst - 1))}s")
        else:
            print(f"  {desc:<26} 源0帧 -> 输出0帧（空进空出）")


if __name__ == "__main__":
    print("=" * 100)
    print("帧率转换对拍报告 — 转换方式：最近邻重采样（丢帧/复制），时间戳为精确有理数")
    print("=" * 100)
    for name, c in CASES:
        show_case(name, c)
    show_drift()
    show_edges()
