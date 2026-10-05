"""自适应追踪采样器自测 + 数据产出（仅标准库）。

运行:
    python3 test_adaptive_sampler.py            # 自测 + 全部数据
    python3 -m unittest test_adaptive_sampler   # 只跑断言
"""

from __future__ import annotations

import random
import time
import tracemalloc
import unittest

from adaptive_sampler import AdaptiveSampler, hash_unit_interval

REPORT: list[str] = []


def log(msg: str = "") -> None:
    REPORT.append(msg)
    print(msg)


def section(title: str) -> None:
    log("")
    log("=" * 72)
    log(title)
    log("=" * 72)


# ---------------------------------------------------------------------------
# 场景一：流量平稳 —— 稳态采样率贴合上限
# ---------------------------------------------------------------------------

class TestSteadyTraffic(unittest.TestCase):
    def test_rate_matches_capacity(self) -> None:
        sampler = AdaptiveSampler(max_traces_per_sec=10_000)
        # 流量远低于上限：全采
        self.assertAlmostEqual(sampler.compute_rate(rps=5_000, error_rate=0.0), 1.0)
        # 流量 = 上限：全采
        self.assertAlmostEqual(sampler.compute_rate(rps=10_000, error_rate=0.0), 1.0)
        # 流量 5 倍于上限：约 1/5
        self.assertAlmostEqual(sampler.compute_rate(rps=50_000, error_rate=0.0), 0.2, places=6)
        # 10 倍：1/10，且不低于 min_rate
        r = sampler.compute_rate(rps=100_000, error_rate=0.0)
        self.assertAlmostEqual(r, 0.1, places=6)
        r = sampler.compute_rate(rps=100_000_000, error_rate=0.0)
        self.assertGreaterEqual(r, sampler.min_rate)

        # 端到端：先用一个窗口让控制器观测到 50000 rps，第二个窗口验证保留量
        clock = [0.0]
        sampler = AdaptiveSampler(max_traces_per_sec=10_000, window_sec=1.0,
                                  time_source=lambda: clock[0])
        for i in range(50_000):  # 窗口 1：观测期
            sampler.should_sample(f"warm-{i}")
        clock[0] += 1.0          # 窗口结束 -> rate 调整为 0.2
        kept = sum(sampler.should_sample(f"t-{i}", has_error=False) for i in range(50_000))
        self.assertAlmostEqual(kept / 50_000, 0.2, delta=0.005)
        self.assertLess(abs(kept - 10_000), 250)


# ---------------------------------------------------------------------------
# 数据：调整曲线（当前量 -> 采样率；不同错误率）
# ---------------------------------------------------------------------------

class TestAdjustmentCurve(unittest.TestCase):
    def test_print_curve(self) -> None:
        section("数据 1：采样率调整曲线（上限 max_tps = 10000，min_rate=0.001）")
        sampler = AdaptiveSampler(max_traces_per_sec=10_000)
        log(f"{'当前量(rps)':>12} | {'错误率0%':>8} | {'错误率2%':>8} | {'错误率10%':>9}")
        log("-" * 50)
        rps_list = [
            1_000, 3_000, 5_000, 8_000, 10_000,
            15_000, 20_000, 30_000, 50_000, 100_000, 500_000, 1_000_000,
        ]
        for rps in rps_list:
            r0 = sampler.compute_rate(rps, 0.0)
            r2 = sampler.compute_rate(rps, 0.02)
            r10 = sampler.compute_rate(rps, 0.10)
            log(f"{rps:>12,} | {r0:>8.4f} | {r2:>8.4f} | {r10:>9.4f}")

        log("")
        log("ASCII 曲线（错误率 0%，采样率随当前量变化，每格 = 2%）：")
        for rps in rps_list:
            r = sampler.compute_rate(rps, 0.0)
            bar = "#" * round(r * 50)
            log(f"{rps:>10,} | {bar} {r:.3f}")


# ---------------------------------------------------------------------------
# 场景二：流量突增 / 突降 —— 响应时间
# ---------------------------------------------------------------------------

class TestBurstResponse(unittest.TestCase):
    def test_spike_and_drop_response(self) -> None:
        section("数据 2：流量突增/突降响应时间（调整窗口 = 1s，模拟时钟）")
        clock = [0.0]
        sampler = AdaptiveSampler(max_traces_per_sec=10_000, window_sec=1.0,
                                  time_source=lambda: clock[0])

        def advance(seconds: float, rps: float) -> None:
            # 每个窗口内按 rps 灌入链路
            steps = int(round(seconds / sampler.window_sec))
            for _ in range(steps):
                for _ in range(int(rps * sampler.window_sec)):
                    sampler.should_sample(f"t-{random.random()}")
                clock[0] += sampler.window_sec
                sampler._maybe_adjust(clock[0])

        # 稳态 5000 rps
        advance(2, 5_000)
        rate_stable = sampler.rate
        log(f"稳态 5,000 rps（低于上限 10,000）       -> rate = {rate_stable:.3f}")
        self.assertAlmostEqual(rate_stable, 1.0)

        # 突增到 100,000 rps（20 倍）
        t0 = clock[0]
        advance(1, 100_000)
        spike_lag = clock[0] - t0
        rate_spike = sampler.rate
        log(f"突增到 100,000 rps（20倍），{spike_lag:.0f}s 后  -> rate = {rate_spike:.4f}"
            f"（理论 0.1000）")
        self.assertAlmostEqual(rate_spike, 0.1, places=3)
        self.assertLessEqual(spike_lag, sampler.window_sec)

        # 突降回 5,000 rps
        t0 = clock[0]
        advance(1, 5_000)
        drop_lag = clock[0] - t0
        rate_drop = sampler.rate
        log(f"突降回 5,000 rps，{drop_lag:.0f}s 后             -> rate = {rate_drop:.3f}（理论 1.000）")
        self.assertAlmostEqual(rate_drop, 1.0)
        self.assertLessEqual(drop_lag, sampler.window_sec)
        log(f"响应时间结论：突增、突降均在 1 个调整窗口（{sampler.window_sec:.0f}s）内完成跟进。")


# ---------------------------------------------------------------------------
# 场景三：错误率骤升 —— 异常链路保留率 + 正常链路上下文增采
# ---------------------------------------------------------------------------

class TestErrorSpike(unittest.TestCase):
    def test_error_retention(self) -> None:
        section("数据 3：异常链路保留率（上限 10000，当前量 50000，窗口 1s）")
        rng = random.Random(42)
        n = 200_000
        for label, error_rate in [("平稳（错误率 1%）", 0.01),
                                  ("错误率骤升（20%）", 0.20)]:
            clock = [0.0]
            sampler = AdaptiveSampler(max_traces_per_sec=10_000, window_sec=1.0,
                                      time_source=lambda: clock[0])
            # 窗口 1：按 50000 rps、指定错误率灌入，让控制器观测并调整
            for i in range(50_000):
                sampler.should_sample(f"warm-{i}", has_error=rng.random() < error_rate)
            clock[0] += 1.0
            sampler._maybe_adjust(clock[0])  # 窗口 1 结束，触发调整
            rate = sampler.rate              # 窗口 2 生效的采样率
            kept_err = kept_ok = 0
            err_total = ok_total = 0
            for i in range(n):
                is_err = rng.random() < error_rate
                if is_err:
                    err_total += 1
                    kept_err += sampler.should_sample(f"e-{label}-{i}", has_error=True)
                else:
                    ok_total += 1
                    kept_ok += sampler.should_sample(f"o-{label}-{i}", has_error=False)
            log(f"  {label}: 正常链路采样率={rate:.4f} | "
                f"正常链路保留 {kept_ok}/{ok_total} = {kept_ok / ok_total:.4%} | "
                f"异常链路保留 {kept_err}/{err_total} = {kept_err / err_total:.4%}")
            self.assertEqual(kept_err, err_total)  # 异常链路 100% 保留
            self.assertAlmostEqual(kept_ok / ok_total, rate, delta=0.01)
        log("结论：异常链路保留率恒为 100%；错误率骤升时正常链路采样率上调以补充上下文。")

    def test_error_rate_boost_curve(self) -> None:
        log("")
        log("错误率对采样率的影响（当前量 20000 rps，上限 10000，阈值 5%）：")
        sampler = AdaptiveSampler(max_traces_per_sec=10_000, error_threshold=0.05)
        log(f"{'错误率':>7} | {'正常链路采样率':>12}")
        log("-" * 26)
        for er in [0.0, 0.01, 0.05, 0.10, 0.15, 0.30]:
            log(f"{er:>6.0%} | {sampler.compute_rate(20_000, er):>12.4f}")
        self.assertAlmostEqual(sampler.compute_rate(20_000, 0.0), 0.5, places=6)
        self.assertAlmostEqual(sampler.compute_rate(20_000, 0.15), 1.0, places=6)


# ---------------------------------------------------------------------------
# 场景四：同一条链路整体采样/整体丢弃（含只有部分节点上报）
# ---------------------------------------------------------------------------

class TestTraceIntegrity(unittest.TestCase):
    def test_partial_reporting_consistency(self) -> None:
        section("数据 4：链路整体性校验（部分节点上报场景）")
        # 强制一个较低的采样率：50000 rps / 上限 10000 -> 0.2
        clock = [0.0]
        sampler = AdaptiveSampler(max_traces_per_sec=10_000, window_sec=1.0,
                                  time_source=lambda: clock[0])
        for _ in range(50_000):
            sampler.should_sample(f"warmup-{random.random()}")
        clock[0] += 1.0
        sampler._maybe_adjust(clock[0])

        rng = random.Random(7)
        n_traces = 50_000
        partial_traces = 0
        violations = 0
        sampled_traces = 0
        spans_in_sampled = 0
        dropped_traces = 0
        spans_in_dropped = 0

        for tid in range(n_traces):
            trace_id = f"trace-{tid}"
            has_error = rng.random() < 0.02
            n_spans = rng.randint(3, 15)
            # 模拟"链路只有部分节点上报"：每个 span 以 70% 概率上报
            reported = [s for s in range(n_spans) if rng.random() < 0.7]
            if not reported:
                continue
            if len(reported) < n_spans:
                partial_traces += 1
            decisions = {sampler.should_sample(trace_id, has_error=has_error)
                         for _ in reported}
            # 同一条链路所有上报节点的决策必须完全一致
            if len(decisions) != 1:
                violations += 1
            if decisions == {True}:
                sampled_traces += 1
                spans_in_sampled += len(reported)
            else:
                dropped_traces += 1
                spans_in_dropped += len(reported)

        log(f"  模拟链路数：{n_traces:,}，其中部分节点上报：{partial_traces:,} 条")
        log(f"  决策不一致（同链路部分节点被采样）的链路数：{violations}")
        log(f"  整体保留：{sampled_traces:,} 条链路 / {spans_in_sampled:,} 个 span，"
            f"保留链路的 span 完整率 = 100%")
        log(f"  整体丢弃：{dropped_traces:,} 条链路 / {spans_in_dropped:,} 个 span，"
            f"丢弃链路无任何 span 混入")
        log(f"  异常链路采样率恒为 100%（见数据 3）。")
        self.assertEqual(violations, 0)
        self.assertGreater(sampled_traces, 0)
        self.assertGreater(dropped_traces, 0)

    def test_hash_stability_across_nodes(self) -> None:
        # 不同"节点"（新实例）对同一 trace_id 判定一致
        nodes = [AdaptiveSampler(max_traces_per_sec=10_000) for _ in range(8)]
        rng = random.Random(1)
        for _ in range(2_000):
            tid = f"x-{rng.randrange(1 << 40):010x}"
            dec = {n.should_sample(tid, has_error=False) for n in nodes}
            self.assertEqual(len(dec), 1)
        log("  跨 8 个独立采样器实例（模拟不同节点）对同一 trace_id 决策一致：2000/2000")


# ---------------------------------------------------------------------------
# 数据 5：100 万条链路决策耗时与内存峰值
# ---------------------------------------------------------------------------

class TestOneMillionPerformance(unittest.TestCase):
    def test_perf_1m(self) -> None:
        section("数据 5：100 万条链路采样决策耗时与内存峰值")
        n = 1_000_000
        trace_ids = [f"perf-trace-{i:08d}" for i in range(n)]

        sampler = AdaptiveSampler(max_traces_per_sec=10_000, window_sec=float("inf"))
        tracemalloc.start()
        t0 = time.perf_counter()
        kept = 0
        for tid in trace_ids:
            kept += sampler.should_sample(tid, has_error=False)
        elapsed = time.perf_counter() - t0
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        log(f"  决策总数：{n:,}（采样率 {sampler.rate:.3f}，保留 {kept:,}）")
        log(f"  总耗时：{elapsed:.3f}s，单决策平均：{elapsed / n * 1e6:.3f} µs，"
            f"吞吐：{n / elapsed:,.0f} 决策/秒")
        log(f"  决策阶段内存峰值（tracemalloc，不含输入 trace_id 列表）："
            f"{peak / 1024:.1f} KiB")
        log("  内存模型：O(1) 单链路状态（只做计数），采样器不存储 trace_id，"
            "100 万链路也不增长。")
        self.assertAlmostEqual(kept / n, 1.0, delta=0.01)  # window=inf -> rate 1.0


if __name__ == "__main__":
    # 先产出数据报告，再用 unittest 断言校验
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(__import__("test_adaptive_sampler"))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
