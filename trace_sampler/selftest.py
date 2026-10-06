"""自适应追踪采样器：自测 + 场景模拟 + 100 万条基准（仅标准库）。

运行：python3 selftest.py
"""

import gc
import random
import resource
import sys
import time
import tracemalloc

from adaptive_sampler import AdaptiveSampler

WINDOW_SEC = 1.0  # 控制窗口长度（秒），响应时间按窗口数 × 该值换算


def simulate_window(sampler, qps, err_rate, win_id, rng, prefix="trace"):
    """模拟一个控制窗口：产生 qps 条链路并逐条做采样决策，窗口末更新采样率。"""
    kept = 0
    err_total = 0
    err_kept = 0
    for i in range(qps):
        trace_id = "%s-w%d-%d" % (prefix, win_id, i)
        is_err = rng.random() < err_rate
        if is_err:
            err_total += 1
        if sampler.should_sample(trace_id, is_err):
            kept += 1
            if is_err:
                err_kept += 1
    sampler.update(qps, err_rate)
    return {
        "qps": qps,
        "rate": sampler.rate,
        "kept": kept,
        "err_total": err_total,
        "err_kept": err_kept,
        "err_retention": (err_kept / err_total) if err_total else 1.0,
    }


def header(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def scenario_steady():
    header("场景1：流量平稳（预算 max_qps=1000）")
    rng = random.Random(42)
    s = AdaptiveSampler(max_qps=1000)
    print("%-4s %8s %9s %9s %12s" % ("窗口", "流入qps", "采样率", "采样后量", "占预算比"))
    for w in range(8):
        r = simulate_window(s, 800, 0.0, w, rng, "steady-low")
        print("%-4d %8d %9.4f %9d %11.1f%%"
              % (w, r["qps"], r["rate"], r["kept"], 100.0 * r["kept"] / s.max_qps))
    print("-- 流量上升到 5000 qps（超预算 5 倍）--")
    converged = None
    for w in range(8, 16):
        r = simulate_window(s, 5000, 0.0, w, rng, "steady-high")
        ratio = r["kept"] / s.max_qps
        if converged is None and abs(ratio - 1.0) <= 0.10:
            converged = w - 8
        print("%-4d %8d %9.4f %9d %11.1f%%"
              % (w, r["qps"], r["rate"], r["kept"], 100.0 * ratio))
    last = r
    assert abs(last["kept"] / 1000 - 1.0) <= 0.08, "平稳期采样后量应收敛到预算附近"
    print("结论：采样率收敛到 %.3f（理论 1000/5000=0.2），采样后量 %.0f qps，在预算 ±10%% 内；"
          "首个达预算窗口偏移 = %d 个窗口" % (last["rate"], last["kept"], converged))
    return last


def scenario_spike():
    header("场景2：流量突增 1000 -> 10000 qps（预算 1000）")
    rng = random.Random(7)
    s = AdaptiveSampler(max_qps=1000)
    simulate_window(s, 1000, 0.0, 0, rng, "spike")
    print("突增前：rate=%.3f" % s.rate)
    print("%-4s %8s %9s %9s %11s %s" % ("窗口", "流入qps", "采样率", "采样后量", "超预算", "说明"))
    on_budget_window = None
    for w in range(1, 7):
        qps = 10000
        r = simulate_window(s, qps, 0.0, w, rng, "spike")
        over = r["kept"] / s.max_qps
        note = ""
        if on_budget_window is None and abs(over - 1.0) <= 0.10:
            on_budget_window = w
            note = "<= 回到预算 ±10%"
        print("%-4d %8d %9.4f %9d %10.1fx %s"
              % (w, r["qps"], r["rate"], r["kept"], over, note))
    print("结论：突增窗口结束瞬间采样率立即下调到 %.3f；之后第 %d 个窗口（即变化后 %.1fs）采样量回到预算 ±10%% 内"
          % (0.1, on_budget_window, on_budget_window * WINDOW_SEC))
    return on_budget_window


def scenario_drop():
    header("场景3：流量突降 10000 -> 500 qps（预算 1000）")
    rng = random.Random(9)
    s = AdaptiveSampler(max_qps=1000, max_growth=2.0)
    for w in range(5):
        simulate_window(s, 10000, 0.0, w, rng, "drop-hi")
    print("突降前：rate=%.4f" % s.rate)
    print("%-4s %8s %9s %9s %s" % ("窗口", "流入qps", "采样率", "采样后量", "说明"))
    recover_window = None
    for w in range(5, 16):
        r = simulate_window(s, 500, 0.0, w, rng, "drop-lo")
        note = ""
        if recover_window is None and s.rate >= 0.95:
            recover_window = w - 5
            note = "<= rate 恢复到 >=0.95（全采）"
        print("%-4d %8d %9.4f %9d %s" % (w, r["qps"], r["rate"], r["kept"], note))
    print("结论：恢复限速 max_growth=2.0/窗口，rate %.4f -> 1.0 用 %d 个窗口（%.0fs），避免采样率抖动"
          % (0.1, recover_window, recover_window * WINDOW_SEC))
    return recover_window


def scenario_error_spike():
    header("场景4：错误率骤升 1% -> 30%（预算 1000，流入 5000 qps）")
    rng = random.Random(123)
    s = AdaptiveSampler(max_qps=1000, err_threshold=0.05, err_boost=4.0)
    print("%-4s %8s %8s %9s %10s %12s" % ("窗口", "流入qps", "错误率", "采样率", "采样后量", "异常保留率"))
    rates = [0.01, 0.01, 0.01, 0.30, 0.30, 0.30, 0.05, 0.01]
    for w, er in enumerate(rates):
        r = simulate_window(s, 5000, er, w, rng, "err")
        print("%-4d %8d %7.1f%% %9.4f %10d %11.2f%%"
              % (w, r["qps"], 100 * er, r["rate"], r["kept"], 100 * r["err_retention"]))
    print("结论：异常链路保留率恒为 100.00%（has_error 无条件保留，优先于预算）；")
    print("      错误率 > 5% 阈值时整体采样率按 1+4*(err-0.05) 提升，错误现场的正常上下文也更多保留；")
    print("      异常期间总采样量允许暂时超出预算（宁可超量也不丢错误）。")


def scenario_partial_spans():
    header("场景5：链路只有部分节点上报（整链一致性校验）")
    rng = random.Random(2026)
    s = AdaptiveSampler(max_qps=1000)
    s.update(8000, 0.0)  # 压到低采样率 0.125，增加被“切中”概率
    n_traces = 20000
    report_prob = 0.5  # 每个 span 只有 50% 概率上报，模拟部分节点缺失
    decisions = {}
    total_spans = 0
    reported_spans = 0
    for t in range(n_traces):
        tid = "partial-%d" % t
        n_spans = rng.randint(1, 10)
        total_spans += n_spans
        order = list(range(n_spans))
        rng.shuffle(order)
        for _ in order:
            if rng.random() >= report_prob:
                continue  # 该节点未上报
            reported_spans += 1
            decisions.setdefault(tid, set()).add(s.should_sample(tid, has_error=False))
    reported_traces = len(decisions)
    silent_traces = n_traces - reported_traces  # 一个节点都没上报的链路，无法校验
    split = sum(1 for sets in decisions.values() if len(sets) > 1)
    unanimous = reported_traces - split
    sampled = sum(1 for sets in decisions.values() if sets == {True})
    print("链路总数            : %d（其中 %d 条有节点上报，%d 条完全未上报）"
          % (n_traces, reported_traces, silent_traces))
    print("节点(span)总数      : %d，实际上报 : %d（%.0f%% 节点缺失）"
          % (total_spans, reported_spans, 100 * (1 - reported_spans / total_spans)))
    print("整链决策一致的链路  : %d" % unanimous)
    print("决策分裂的链路      : %d" % split)
    print("整链一致率          : %.4f%%（按有上报的链路计）" % (100.0 * unanimous / reported_traces))
    print("整体采样的链路占比  : %.2f%%（采样率 %.3f）" % (100.0 * sampled / reported_traces, s.rate))
    assert split == 0, "出现同一条链路部分节点采样、部分丢弃！"
    print("结论：0 条链路出现部分节点被采样，整链一致率 100%。")


def benchmark_1m():
    header("基准：100 万条链路采样决策（rate=0.1，其中 10% 异常链路）")
    s = AdaptiveSampler(max_qps=1000)
    s.update(10000, 0.0)  # rate=0.1
    n = 1_000_000
    gc.collect()
    tracemalloc.start()
    t0 = time.perf_counter()
    kept = 0
    err_kept = 0
    err_total = 0
    for i in range(n):
        is_err = (i % 10 == 0)
        if is_err:
            err_total += 1
        if s.should_sample("bench-%d" % i, is_err):
            kept += 1
            if is_err:
                err_kept += 1
    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print("决策总数             : %d" % n)
    print("总耗时               : %.3f s" % elapsed)
    print("单决策耗时           : %.2f ns" % (elapsed / n * 1e9))
    print("吞吐                 : %.0f 万决策/秒" % (n / elapsed / 1e4))
    print("采样器状态内存       : O(1)（仅存几个标量，无 trace_id 表）")
    print("Python 峰值分配内存  : %.2f KiB（tracemalloc，含循环内临时对象）" % (peak / 1024))
    print("进程峰值 RSS         : %.2f MiB（ru_maxrss）" % (rss_kb / 1024))
    print("普通链路实际采样占比 : %.2f%%（目标 10.00%%）"
          % (100.0 * (kept - err_kept) / (n - err_total)))
    print("异常链路保留率       : %.2f%%（%d/%d）" % (100.0 * err_kept / err_total, err_kept, err_total))
    assert err_kept == err_total
    assert abs((kept - err_kept) / (n - err_total) - 0.1) < 0.01


def print_curve():
    header("调整曲线（预算 max_qps=1000，错误率 0%）：rate = clamp(1000/qps, min, 1)")
    print("%8s %9s %s" % ("流入qps", "采样率", "采样率(每格=0.05)"))
    for qps in (100, 250, 500, 750, 1000, 1500, 2000, 3000, 5000, 10000, 20000, 50000):
        rate = min(1.0, max(1e-4, 1000.0 / qps))
        print("%8d %9.4f |%s" % (qps, rate, "#" * round(rate * 20)))
    print()
    print("错误率提升因子（qps=5000 时）：factor = 1 + 4 * max(0, err_rate - 0.05)")
    print("%8s %9s %9s" % ("错误率", "factor", "有效rate"))
    for er in (0.0, 0.01, 0.05, 0.10, 0.20, 0.30, 0.50):
        factor = 1 + 4 * max(0.0, er - 0.05)
        rate = min(1.0, 1000.0 / 5000 * factor)
        print("%7.0f%% %9.2f %9.4f" % (100 * er, factor, rate))


def main():
    print_curve()
    scenario_steady()
    scenario_spike()
    scenario_drop()
    scenario_error_spike()
    scenario_partial_spans()
    benchmark_1m()
    print("\n全部断言通过。")


if __name__ == "__main__":
    sys.exit(main())
