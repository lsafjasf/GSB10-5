"""sync_correct 自测与性能基准。

运行：python3 selftest.py
覆盖情形：只有固定偏移 / 只有线性漂移 / 两者叠加 / 锚点极少 / 残差过大，
并对 10 万条字幕测量校正耗时与内存峰值。
"""

import random
import resource
import sys
import time
import tracemalloc

from sync_correct import (
    Anchor, FitReport, correct_cues, fit_and_classify, paired_errors, rms,
)


def make_anchors(true_slope, true_intercept, n=40, span=3600.0, noise=0.05, seed=42):
    """生成仿真锚点：t_audio = true_slope * t_sub + true_intercept + 噪声。"""
    rng = random.Random(seed)
    anchors = []
    for i in range(n):
        t_sub = span * (i + 0.5) / n
        t_audio = true_slope * t_sub + true_intercept + rng.uniform(-noise, noise)
        anchors.append(Anchor(t_sub, t_audio))
    return anchors


def print_residuals(report: FitReport, anchors, limit=12):
    print(f"  残差（t_audio - 拟合值），共 {len(report.residuals)} 个在用锚点"
          f"（剔除粗差 {report.n_outliers} 个）:")
    used = [a for i, a in enumerate(anchors) if i not in set(report.outlier_indices)]
    show = list(zip(used, report.residuals))[:limit]
    for a, r in show:
        print(f"    t_sub={a.t_sub:9.2f}s  残差={r:+.4f}s")
    if len(report.residuals) > limit:
        print(f"    ... 其余 {len(report.residuals) - limit} 个略")
    print(f"  残差统计: RMS={report.rms_residual:.4f}s  "
          f"最大绝对值={report.max_abs_residual:.4f}s")


def print_paired_errors(anchors, report, limit=8):
    rows = paired_errors(anchors, report)
    before_rms = rms([r[1] for r in rows])
    after_rms = rms([r[2] for r in rows])
    before_max = max(abs(r[1]) for r in rows)
    after_max = max(abs(r[2]) for r in rows)
    print(f"  校正前后误差成对对比（前 {limit} 个锚点）:")
    print(f"    {'t_sub(s)':>10} {'校正前误差(s)':>14} {'校正后误差(s)':>14}")
    for t_sub, before, after in rows[:limit]:
        print(f"    {t_sub:10.2f} {before:+14.4f} {after:+14.4f}")
    print(f"  汇总: 校正前 RMS={before_rms:.4f}s/max={before_max:.4f}s -> "
          f"校正后 RMS={after_rms:.4f}s/max={after_max:.4f}s "
          f"(RMS 改善 {before_rms / after_rms if after_rms > 1e-12 else float('inf'):.1f}x)")
    return before_rms, after_rms


def run_case(title, anchors, expect_kind=None, show_residuals=True):
    print(f"\n=== 用例: {title} ===")
    print(f"  锚点数: {len(anchors)}")
    report = fit_and_classify(anchors)
    print(f"  判定结果: {report.kind}")
    print(f"  依据说明: {report.reason}")
    print(f"  拟合参数: slope={report.slope:.6f} (SE={report.slope_stderr:.2e}), "
          f"intercept={report.intercept:+.4f}s (SE={report.intercept_stderr:.2e}), "
          f"接受={report.accepted}")
    if show_residuals and report.residuals:
        print_residuals(report, anchors)
    if report.accepted and report.kind != "in_sync":
        print_paired_errors(anchors, report)
    if expect_kind is not None:
        assert report.kind == expect_kind, f"期望 {expect_kind}，实际 {report.kind}"
    return report


def case_fixed_offset():
    anchors = make_anchors(true_slope=1.0, true_intercept=2.5)
    report = run_case("只有固定偏移（音频比字幕晚 2.5s）", anchors, "fixed_offset")
    assert abs(report.slope - 1.0) < 1e-9
    assert abs(report.intercept - 2.5) < 0.05


def case_linear_drift():
    anchors = make_anchors(true_slope=1.002, true_intercept=0.0)
    report = run_case("只有线性漂移（每小时快 7.2s）", anchors, "linear_drift")
    assert abs(report.slope - 1.002) < 2e-4


def case_both():
    anchors = make_anchors(true_slope=1.0015, true_intercept=-1.2)
    report = run_case("固定偏移 + 线性漂移叠加（早 1.2s 且每小时慢 5.4s）",
                      anchors, "offset_and_drift")
    assert abs(report.slope - 1.0015) < 2e-4
    assert abs(report.intercept + 1.2) < 0.1


def case_too_few_anchors():
    anchors = make_anchors(true_slope=1.001, true_intercept=1.0, n=3, seed=7)
    report = run_case("锚点极少（仅 3 个）", anchors, "rejected_too_few_anchors",
                      show_residuals=False)
    assert not report.accepted
    try:
        correct_cues([(0.0, 1.0)], report)
        raise AssertionError("应当对拒绝的报告抛出异常")
    except ValueError:
        pass


def case_poor_fit():
    # 中途跳变：前半段偏移 +1s，后半段偏移 +6s，线性模型不成立
    rng = random.Random(99)
    anchors = []
    for i in range(40):
        t_sub = 3600.0 * (i + 0.5) / 40
        offset = 1.0 if t_sub < 1800.0 else 6.0
        anchors.append(Anchor(t_sub, t_sub + offset + rng.uniform(-0.05, 0.05)))
    report = run_case("残差过大（中途跳变，非线性误差）", anchors, "rejected_poor_fit")
    assert not report.accepted
    assert report.rms_residual > 0.30


def case_outliers():
    # 40 个正常锚点 + 3 个标注错误的粗差，应被 MAD 剔除后正常拟合
    anchors = make_anchors(true_slope=1.0, true_intercept=2.0, n=40, seed=5)
    anchors[10] = Anchor(anchors[10].t_sub, anchors[10].t_audio + 8.0)
    anchors[25] = Anchor(anchors[25].t_sub, anchors[25].t_audio - 6.0)
    anchors[33] = Anchor(anchors[33].t_sub, anchors[33].t_audio + 5.0)
    report = run_case("含 3 个粗差锚点（MAD 剔除后拟合）", anchors, "fixed_offset")
    assert report.n_outliers == 3
    assert abs(report.intercept - 2.0) < 0.05


def case_perf_100k():
    print("\n=== 性能: 10 万条字幕校正 ===")
    n = 100_000
    cues = [(i * 3.0, i * 3.0 + 2.0) for i in range(n)]
    anchors = make_anchors(true_slope=1.0015, true_intercept=-1.2)
    report = fit_and_classify(anchors)
    assert report.accepted

    tracemalloc.start()
    t0 = time.perf_counter()
    corrected = correct_cues(cues, report)
    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    maxrss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    print(f"  条数: {len(corrected)}")
    print(f"  校正耗时: {elapsed * 1000:.1f} ms ({n / elapsed / 1e6:.2f} 百万条/秒)")
    print(f"  Python 堆内存峰值(tracemalloc): {peak / 1024 / 1024:.1f} MiB")
    print(f"  进程峰值 RSS(getrusage): {maxrss_mb:.1f} MiB")
    # 抽查一条
    s, e = corrected[99999]
    exp_s = report.slope * (99999 * 3.0) + report.intercept
    assert abs(s - exp_s) < 1e-9 and e > s


def main():
    print(f"Python {sys.version.split()[0]}")
    case_fixed_offset()
    case_linear_drift()
    case_both()
    case_too_few_anchors()
    case_poor_fit()
    case_outliers()
    case_perf_100k()
    print("\n全部自测通过 ✔")


if __name__ == "__main__":
    main()
