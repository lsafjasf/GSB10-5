"""自测：固定偏移 / 仅漂移 / 两者叠加 / 锚点极少 / 残差异常 + 10万条性能。"""

import random
import time
import tracemalloc

from sync_correct import (Anchor, fit, paired_errors, rmse_of,
                          parse_srt, correct_cues, dump_srt)

SPAN = 3600.0          # 锚点覆盖 1 小时
N_ANCHORS = 20
NOISE = 0.04           # 锚点匹配噪声 ±40ms


def make_anchors(offset, scale, n=N_ANCHORS, span=SPAN, noise=NOISE,
                 seed=1, outlier=False):
    rng = random.Random(seed)
    ts = [span * (i + 1) / (n + 1) for i in range(n)]
    anchors = []
    for i, t in enumerate(ts):
        y = offset + scale * t + rng.uniform(-noise, noise)
        if outlier and i == n // 2:
            y += 2.5  # 离群锚点
        anchors.append(Anchor(t, y))
    return anchors


def print_residuals(res):
    print("  残差数据（每个锚点，秒）:")
    line = ", ".join(f"{r:+.3f}" for r in res.residuals)
    print(f"    [{line}]")
    print(f"  RMSE={res.rmse:.4f}s  max|residual|={res.max_abs_residual:.4f}s")


def print_paired(res, anchors):
    before, after = paired_errors(res, anchors)
    print("  校正前后误差成对对比（秒）:")
    print("    anchor_t   before    after")
    for a, eb, ea in zip(sorted(anchors, key=lambda x: x.sub_time), before, after):
        print(f"    {a.sub_time:8.1f}  {eb:+8.3f}  {ea:+8.3f}")
    rb, ra = rmse_of(before), rmse_of(after)
    improve = (1 - ra / rb) * 100 if rb else 0.0
    print(f"  RMSE: {rb:.4f}s -> {ra:.4f}s，改善 {improve:.1f}%")
    return rb, ra


def run_scenario(name, anchors, expect_kind=None):
    print("=" * 68)
    print(f"情形: {name}")
    res = fit(anchors)
    print(f"  锚点数={res.n_anchors} 跨度={res.span:.0f}s")
    print(f"  判定结果: {res.kind}  ok={res.ok}")
    if res.kind == "rejected":
        print(f"  拒绝原因: {res.reason}")
        return None
    print(f"  offset={res.offset:+.4f}s  scale={res.scale:.7f} "
          f"漂移率={res.drift_ppm:+.1f}ppm（每秒偏 {res.scale-1:+.2e}s）")
    print_residuals(res)
    if not res.ok:
        print(f"  拒绝应用: {res.reason}")
    print_paired(res, anchors)
    if expect_kind:
        assert res.kind == expect_kind, f"{name}: 期望 {expect_kind} 实际 {res.kind}"
    return res


def benchmark(n=100_000):
    print("=" * 68)
    print(f"性能测试: {n} 条字幕")
    # 生成 SRT：每条间隔 2.4s（约 66.7 小时内容），含偏移+漂移
    blocks = []
    for i in range(1, n + 1):
        s = i * 2.4
        blocks.append(f"{i}\n{_ts(s)} --> {_ts(s+2.0)}\nline {i}\n")
    srt_text = "\n".join(blocks)

    tracemalloc.start()
    t0 = time.perf_counter()
    cues = parse_srt(srt_text)
    res = fit(make_anchors(2.5, 1.0015, seed=7))
    assert res.ok
    corrected = correct_cues(cues, res)
    out_text = dump_srt(corrected)
    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    t_corr = _time_correct_only(cues, res)
    print(f"  字幕条数: {len(cues)}")
    print(f"  解析+校正+序列化总耗时: {elapsed*1000:.1f} ms")
    print(f"  仅校正 10 万条耗时: {t_corr*1000:.2f} ms")
    print(f"  Python 层内存峰值（含 SRT 文本/解析/输出）: {peak/1e6:.1f} MB")
    print(f"  输出前 60 字符: {out_text[:60]!r}")
    assert len(cues) == n
    return elapsed, peak


def _ts(t):
    from sync_correct import format_timestamp
    return format_timestamp(t)


def _time_correct_only(cues, res):
    t0 = time.perf_counter()
    correct_cues(cues, res)
    return time.perf_counter() - t0


def main():
    # 1) 只有固定偏移（整体晚 2.5s）
    run_scenario("仅固定偏移 +2.5s", make_anchors(2.5, 1.0, seed=1),
                 expect_kind="fixed_offset")
    # 2) 只有漂移（0.2%，1 小时偏 7.2s）
    run_scenario("仅线性漂移 scale=1.002", make_anchors(0.0, 1.002, seed=2),
                 expect_kind="linear_drift")
    # 3) 两者叠加（晚 1.2s 且每小时再漂 5.4s）
    run_scenario("偏移+漂移叠加 (1.2s, 1.0015)",
                 make_anchors(1.2, 1.0015, seed=3),
                 expect_kind="linear_drift")
    # 4) 锚点极少（3 个，少于 5 个下限）
    r = run_scenario("锚点极少（3 个）", make_anchors(1.0, 1.002, n=3, seed=4))
    assert r is None, "锚点不足必须拒绝"
    # 5) 残差异常：混入离群锚点
    r = run_scenario("残差异常（1 个锚点偏 2.5s）",
                     make_anchors(1.0, 1.001, seed=5, outlier=True))
    assert r is not None and not r.ok, "残差过大必须报告且不应用"
    print("=" * 68)
    print("全部断言通过")
    benchmark(100_000)


if __name__ == "__main__":
    main()
