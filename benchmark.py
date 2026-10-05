"""千万级基准：1000 万条查询、1% 采样率下的处理耗时与内存峰值。

用法：
  python3 benchmark.py                 # 父进程：依次跑 truth/uniform/pps 三个阶段并汇总
  python3 benchmark.py PHASE [--mem]   # 子进程：跑单个阶段（--mem 时开 tracemalloc 测内存）

每个阶段独立子进程运行，保证 ru_maxrss 互不污染。
计时在不开 tracemalloc 的运行中进行（避免测量开销干扰耗时）。
"""

import json
import resource
import subprocess
import sys
import time
from array import array

import slowquery as sq

N = 10_000_000
RATE = 0.01
SEED = 20261005
SERVICES = ["gateway", "order", "user", "search", "report"]
SERVICE_W = [0.40, 0.25, 0.15, 0.12, 0.08]
TAIL_P = [0.005, 0.008, 0.007, 0.015, 0.030]
QS = [0.5, 0.9, 0.99, 0.999]


def stream_records(n, seed):
    """流式生成与 experiments.py 相同分布的标准负载（同一 seed 各阶段数据一致）。"""
    import random
    rng = random.Random(seed)
    cum = [0.0]
    for w in SERVICE_W:
        cum.append(cum[-1] + w)
    logn = rng.lognormvariate
    rand = rng.random
    for _ in range(n):
        r = rand()
        sid = 0
        while sid < 4 and r > cum[sid + 1]:
            sid += 1
        u = rand()
        if u < 0.94:
            d = logn(4.6, 0.5)
        elif u < 1.0 - TAIL_P[sid]:
            d = logn(6.2, 0.6)
        else:
            d = logn(7.6, 0.7)
        yield d, sid


def rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def run_phase(phase, measure_mem):
    if measure_mem:
        import tracemalloc
        tracemalloc.start()

    out = {"phase": phase}

    if phase == "truth":
        # 全量精确计算：必须物化全部 1000 万条并排序
        t0 = time.perf_counter()
        durs = []  # 故意用 list，展示全量基线的真实内存代价
        svc_sum = [0.0] * 5
        svc_cnt = [0] * 5
        for d, sid in stream_records(N, SEED):
            durs.append(d)
            svc_sum[sid] += d
            svc_cnt[sid] += 1
        t1 = time.perf_counter()
        durs.sort()
        quant = sq.exact_quantiles(durs, QS)
        mean = sum(durs) / len(durs)
        t2 = time.perf_counter()
        out.update({
            "mean": mean,
            "quantiles": dict(zip([str(q) for q in QS], quant)),
            "max": durs[-1],
            "svc_total": svc_sum,
            "svc_count": svc_cnt,
            "t_generate_collect_s": t1 - t0,
            "t_sort_quantile_s": t2 - t1,
            "t_total_s": t2 - t0,
        })
    else:
        sampler = (sq.UniformSampler(RATE, seed=SEED) if phase == "uniform"
                   else sq.PPSDurationSampler(RATE, seed=SEED, pilot_size=65536))
        t0 = time.perf_counter()
        for d, sid in stream_records(N, SEED):
            sampler.offer(d, sid)
        t1 = time.perf_counter()
        items = sampler.items
        wd = sq.WeightedDistribution(items)
        mu, mu_lo, mu_hi, _ = sq.estimate_mean(items)
        quant = {str(q): wd.quantile(q) for q in QS}
        quant_ci = {str(q): wd.quantile_ci(q) for q in QS}
        groups = sq.groupby(items, lambda sid: SERVICES[sid])
        warns = sq.reliability_warnings(len(items), sq.effective_sample_size(items), QS)
        t2 = time.perf_counter()
        out.update({
            "n_kept": len(items),
            "ess": sq.effective_sample_size(items),
            "mean": mu,
            "mean_ci": [mu_lo, mu_hi],
            "quantiles": quant,
            "quantile_cis": {k: list(v) for k, v in quant_ci.items()},
            "groupby": groups,
            "warnings": warns,
            "t_sample_loop_s": t1 - t0,
            "t_estimate_s": t2 - t1,
            "t_total_s": t2 - t0,
        })
        if phase == "pps":
            out["tau"] = sampler.tau

    if measure_mem:
        import tracemalloc
        _, peak = tracemalloc.get_traced_memory()
        out["peak_python_heap_mb"] = peak / 1024 / 1024
        tracemalloc.stop()
    out["peak_rss_mb"] = rss_mb()
    return out


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("truth", "uniform", "pps"):
        phase = sys.argv[1]
        measure_mem = "--mem" in sys.argv
        print(json.dumps(run_phase(phase, measure_mem)))
        return

    # 父进程：每个阶段跑两遍（一遍计时、一遍测内存）
    results = {}
    for phase in ("truth", "uniform", "pps"):
        for mem in (False, True):
            cmd = [sys.executable, "benchmark.py", phase] + (["--mem"] if mem else [])
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                sys.stderr.write(r.stderr)
                sys.exit(1)
            rec = json.loads(r.stdout)
            key = phase + ("_mem" if mem else "")
            results[key] = rec
            print("阶段 %-7s %s 完成: 总耗时 %.1fs, RSS 峰值 %.0f MB"
                  % (phase, "(测内存)" if mem else "(计时)",
                     rec["t_total_s"], rec["peak_rss_mb"]))

    truth = results["truth"]
    print("\n========== 1000 万条 / 采样率 1% 基准汇总 ==========")
    print("真值: mean=%.2fms  %s  max=%.0fms" % (
        truth["mean"],
        "  ".join("p%g=%.1f" % (float(q) * 100, v)
                  for q, v in truth["quantiles"].items()),
        truth["max"]))
    print("\n%-22s %14s %14s %14s" % ("阶段", "处理耗时(s)", "Python堆峰值MB", "RSS峰值MB"))
    for phase in ("truth", "uniform", "pps"):
        t = results[phase]
        m = results[phase + "_mem"]
        print("%-22s %14.1f %14.0f %14.0f" % (
            {"truth": "全量精确计算(基线)", "uniform": "均匀采样1%",
             "pps": "PPS耗时采样1%"}[phase],
            t["t_total_s"], m.get("peak_python_heap_mb", 0), m["peak_rss_mb"]))

    print("\n%-8s %12s %12s %12s %12s" % ("指标", "真值", "均匀采样", "PPS采样", "PPS的95%CI"))
    for label, key in [("mean", "mean")] + [(("p%g" % (float(q) * 100)), q)
                                            for q in truth["quantiles"]]:
        tv = truth["mean"] if key == "mean" else truth["quantiles"][key]
        uv = results["uniform"]["mean"] if key == "mean" else results["uniform"]["quantiles"][key]
        pv = results["pps"]["mean"] if key == "mean" else results["pps"]["quantiles"][key]
        ci = ""
        if key == "mean":
            ci = "[%.1f, %.1f]" % tuple(results["pps"]["mean_ci"])
        else:
            lo, hi = results["pps"]["quantile_cis"][key]
            ci = "[%.1f, %.1f]" % (lo, hi)
        print("%-8s %12.2f %12.2f %12.2f %24s" % (label, tv, uv, pv, ci))

    print("\n均匀采样告警: %s" % (results["uniform"]["warnings"] or "无"))
    print("PPS采样告警: %s" % (results["pps"]["warnings"] or "无"))
    print("PPS 标定 tau=%.1fms（耗时超过 tau 的查询必然入样）" % results["pps"]["tau"])

    print("\n按服务归因（PPS 样本加权还原 vs 全量真值，总耗时占比%）:")
    g = results["pps"]["groupby"]
    grand_est = sum(v["est_total_ms"] for v in g.values())
    grand_true = sum(truth["svc_total"])
    for i, svc in enumerate(SERVICES):
        print("  %-8s 真实 %5.2f%%  估计 %5.2f%%" % (
            svc, truth["svc_total"][i] / grand_true * 100,
            g[svc]["est_total_ms"] / grand_est * 100))

    with open("results/benchmark.json", "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\n结果已写入 results/benchmark.json")


if __name__ == "__main__":
    main()
