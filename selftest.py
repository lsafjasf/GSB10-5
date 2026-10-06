"""selftest -- fps_convert 的自测与对拍数据生成。

用法:
    python3 selftest.py            # 生成对拍 CSV 到 ./out/ 并打印摘要，然后跑单元测试
    python3 -m unittest selftest -v   # 只跑单元测试

对拍方式（双重独立校验）:
  1. 网格对拍: 输出第 k 帧 pts 必须严格等于 pts0 + k/dst_rate（精确分数比较）。
  2. 独立参考实现: 用暴力 argmin（与库内贪心推进不同的代码路径）重算每个
     目标时槽应选的源帧，逐帧比对 source_index。
"""

import csv
import os
import unittest
from fractions import Fraction

from fps_convert import (Frame, convert_fps, convert_fps_list, uniform_frames,
                         expected_output_count)

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")


def reference_select(src_pts, t_k):
    """独立参考实现：暴力 argmin |pts - t_k|，等距平局取偶数下标。"""
    best, best_d = 0, None
    for i, p in enumerate(src_pts):
        d = abs(p - t_k)
        if (best_d is None or d < best_d
                or (d == best_d and i % 2 == 0 and best % 2 != 0)):
            best, best_d = i, d
    return best


def run_scenario(name, src_pts, src_rate, dst_rate, duration=None, write_csv=True):
    """跑一个场景：转换 + 双重对拍，返回统计并写 CSV。"""
    src_rate, dst_rate = Fraction(src_rate), Fraction(dst_rate)
    frames = [Frame(p, i) for i, p in enumerate(src_pts)]
    out = convert_fps_list(frames, src_rate, dst_rate, duration)
    pts0 = src_pts[0]

    grid_ok = True
    ref_ok = True
    cum = Fraction(0)
    max_abs_phase = Fraction(0)
    max_abs_cum = Fraction(0)
    rows = []
    for f in out:
        expect_pts = pts0 + f.target_index / dst_rate
        if f.pts != expect_pts:
            grid_ok = False
        ref_idx = reference_select(src_pts, f.pts)
        # 显式 duration 截断/补尾时参考实现同样适用（argmin 语义一致）
        if f.source_index != ref_idx:
            ref_ok = False
        cum += f.phase_error
        max_abs_phase = max(max_abs_phase, abs(f.phase_error))
        max_abs_cum = max(max_abs_cum, abs(cum))
        rows.append({
            "target_index": f.target_index,
            "target_pts_exact": str(f.pts),
            "target_pts_s": f"{float(f.pts):.6f}",
            "source_index": f.source_index,
            "source_pts_exact": str(f.source_pts),
            "source_pts_s": f"{float(f.source_pts):.6f}",
            "phase_error_exact": str(f.phase_error),
            "phase_error_ms": f"{float(f.phase_error) * 1000:+.4f}",
            "cum_error_exact": str(cum),
            "cum_error_ms": f"{float(cum) * 1000:+.4f}",
            "duplicated": int(f.duplicated),
        })

    stats = {
        "name": name,
        "src_count": len(src_pts),
        "dst_count": len(out),
        "expected_count": expected_output_count(len(src_pts), src_rate, dst_rate, duration),
        "grid_ok": grid_ok,
        "ref_ok": ref_ok,
        "max_abs_phase_ms": float(max_abs_phase) * 1000,
        "max_abs_cum_ms": float(max_abs_cum) * 1000,
        "final_cum_ms": float(cum) * 1000,
        "bound_interior_ms": float(src_rate and Fraction(1, 2) / src_rate) * 1000,
        "bound_tail_ms": float(Fraction(1, 1) / src_rate) * 1000,
        "rows": rows,
    }
    assert stats["dst_count"] == stats["expected_count"], \
        f"{name}: 帧数 {stats['dst_count']} != 期望 {stats['expected_count']}"

    if write_csv:
        os.makedirs(OUT_DIR, exist_ok=True)
        path = os.path.join(OUT_DIR, f"{name}.csv")
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    return stats


def uniform_pts(pts0, rate, count):
    rate = Fraction(rate)
    return [Fraction(pts0) + Fraction(i, 1) / rate for i in range(count)]


SCENARIOS = [
    # (名称, 源pts, 源帧率, 目标帧率, duration)
    ("up_int_24_to_48",      uniform_pts(0, 24, 8),          24,               48,               None),
    ("down_int_60_to_30",    uniform_pts(0, 60, 12),         60,               30,               None),
    ("up_nonint_24_to_60",   uniform_pts(0, 24, 25),         24,               60,               None),
    ("down_nonint_60_to_24", uniform_pts(0, 60, 60),         60,               24,               None),
    ("ntsc_23976_to_25",     uniform_pts(0, Fraction(24000, 1001), 240),
     Fraction(24000, 1001), 25, None),
    ("offset_start_25_to_30", uniform_pts(Fraction(3, 2), 25, 50), 25,         30,               None),
    ("tiny_single_frame",    uniform_pts(Fraction(5), 30, 1), 30,              60,               None),
    ("tiny_two_frames",      uniform_pts(0, 30, 2),          30,               60,               None),
    ("explicit_duration",    uniform_pts(0, 24, 10),         24,               60,               Fraction(1, 2)),
]


def generate_all():
    summaries = []
    for name, pts, sr, dr, dur in SCENARIOS:
        s = run_scenario(name, pts, sr, dr, dur)
        summaries.append(s)
        print(f"[{name}] 源{s['src_count']}帧 -> 输出{s['dst_count']}帧 "
              f"(期望{s['expected_count']}) 网格对拍={'OK' if s['grid_ok'] else 'FAIL'} "
              f"参考对拍={'OK' if s['ref_ok'] else 'FAIL'} "
              f"max|相位误差|={s['max_abs_phase_ms']:.4f}ms "
              f"max|累计|={s['max_abs_cum_ms']:.4f}ms 末帧累计={s['final_cum_ms']:+.4f}ms "
              f"(上界: 中间帧{s['bound_interior_ms']:.3f}ms / 尾部{s['bound_tail_ms']:.3f}ms)")
    return summaries


# ---------------- 单元测试 ----------------

class TestConvert(unittest.TestCase):
    def test_all_scenarios_grid_and_ref(self):
        for name, pts, sr, dr, dur in SCENARIOS:
            with self.subTest(name=name):
                s = run_scenario(name, pts, sr, dr, dur, write_csv=False)
                self.assertTrue(s["grid_ok"], "输出 pts 未对齐目标网格")
                self.assertTrue(s["ref_ok"], "与独立参考实现不一致")
                self.assertEqual(s["dst_count"], s["expected_count"])

    def test_phase_bound(self):
        # 默认帧数规则下：任意帧 |相位误差| < 1*Ts（中间帧 <= 0.5*Ts）。
        # 显式 duration 超出素材时长时，尾部误差上界为 duration - 末帧偏移。
        for name, pts, sr, dr, dur in SCENARIOS:
            with self.subTest(name=name):
                ts = Fraction(1, 1) / Fraction(sr)
                if dur is None:
                    bound = ts
                else:
                    bound = max(ts, Fraction(dur) - (pts[-1] - pts[0]))
                for f in convert_fps([Frame(p) for p in pts], sr, dr, dur):
                    self.assertLessEqual(abs(f.phase_error), bound)

    def test_monotonic_and_anchored(self):
        out = convert_fps_list(uniform_frames(Fraction(7, 2), 25, 100), 25, 60)
        self.assertEqual(out[0].pts, Fraction(7, 2))          # 锚定首帧 pts
        self.assertEqual(out[0].source_index, 0)
        for a, b in zip(out, out[1:]):
            self.assertEqual(b.pts - a.pts, Fraction(1, 60))  # 严格匀速
            self.assertGreaterEqual(b.source_index, a.source_index)  # 单调不回头

    def test_streaming_matches_list(self):
        src = list(uniform_frames(0, 24, 50))
        streamed = list(convert_fps(iter(src), 24, 60))
        self.assertEqual([f.source_index for f in streamed],
                         [f.source_index for f in convert_fps_list(src, 24, 60)])

    def test_empty_and_invalid(self):
        self.assertEqual(convert_fps_list([], 24, 60), [])
        with self.assertRaises(ValueError):
            list(convert_fps([Frame(0)], 0, 60))
        with self.assertRaises(ValueError):
            list(convert_fps([Frame(0)], 24, 60, duration=0))

    def test_exact_rational_no_float_drift(self):
        # 216000 帧（1 小时 @60fps）后 pts 仍是精确分数
        out = convert_fps(uniform_frames(0, 60, 216000), 60, 24)
        last = None
        for last in out:
            pass
        self.assertEqual(last.pts, last.target_index * Fraction(1, 24))


if __name__ == "__main__":
    print("=== 场景对拍 ===")
    generate_all()
    print(f"\nCSV 已写入 {OUT_DIR}/")
    print("\n=== 单元测试 ===")
    unittest.main(argv=["selftest", "-v"], exit=False)
