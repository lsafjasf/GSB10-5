"""对比实验：加权还原分布 vs 全量真实分布。

场景：
  A 标准负载 + 均匀采样 1%
  B 标准负载 + 按耗时 PPS 采样 1%
  C 极端长尾负载 + 均匀采样 1%
  D 极端长尾负载 + 按耗时 PPS 采样 1%
  E 标准负载 + 均匀采样 0.02%（小样本，约 200 条，演示置信区间与告警）

运行：python3 experiments.py   （结果写入 results/experiments.json）
"""

import json
import math
import random
import statistics
from array import array

import slowquery as sq

N = 1_000_000
SERVICES = ["gateway", "order", "user", "search", "report"]
SERVICE_W = [0.40, 0.25, 0.15, 0.12, 0.08]
QS = [0.5, 0.9, 0.99, 0.999]
QS_TAIL = QS + [0.9999]


def gen_standard(n, seed):
    """标准负载：94% 快查询 / 5% 慢查询 / 1% 很慢查询（对数正态混合）。"""
    rng = random.Random(seed)
    durs = array("d")
    sids = array("b")
    cum = [0.0]
    for w in SERVICE_W:
        cum.append(cum[-1] + w)
    # 各服务的极慢查询占比不同（report 最慢），用于归因验证
    tail_p = [0.005, 0.008, 0.007, 0.015, 0.030]
    for _ in range(n):
        r = rng.random()
        sid = 0
        while sid < 4 and r > cum[sid + 1]:
            sid += 1
        u = rng.random()
        if u < 0.94:
            d = rng.lognormvariate(4.6, 0.5)    # 中位约 99ms
        elif u < 1.0 - tail_p[sid]:
            d = rng.lognormvariate(6.2, 0.6)    # 中位约 493ms
        else:
            d = rng.lognormvariate(7.6, 0.7)    # 中位约 1998ms
        durs.append(d)
        sids.append(sid)
    return durs, sids


def gen_extreme_tail(n, seed):
    """极端长尾：99.9% 普通查询 + 0.1% Pareto(alpha=1.5) 怪兽查询。"""
    rng = random.Random(seed)
    durs = array("d")
    sids = array("b")
    cum = [0.0]
    for w in SERVICE_W:
        cum.append(cum[-1] + w)
    monster_p = [0.0005, 0.0008, 0.0007, 0.0015, 0.003]
    for _ in range(n):
        r = rng.random()
        sid = 0
        while sid < 4 and r > cum[sid + 1]:
            sid += 1
        u = rng.random()
        if u >= monster_p[sid]:
            d = rng.lognormvariate(4.6, 0.5)
        else:
            d = rng.paretovariate(1.5) * 2000.0  # 最小 2s，均值 6s，方差极大
        durs.append(d)
        sids.append(sid)
    return durs, sids


def truth_stats(durs, qs):
    xs = sorted(durs)
    qv = sq.exact_quantiles(xs, qs)
    return {
        "n": len(durs),
        "mean": sum(durs) / len(durs),
        "quantiles": {str(q): v for q, v in zip(qs, qv)},
        "max": xs[-1],
    }


def run_trials(durs, sids, make_sampler, qs, trials, bootstrap=False):
    """重复采样 trials 次，返回每次的点估计与区间。"""
    recs = []
    for t in range(trials):
        sampler = make_sampler(t)
        offer = sampler.offer
        for d, sid in zip(durs, sids):
            offer(d, sid)
        items = sampler.items
        n = len(items)
        wd = sq.WeightedDistribution(items)
        mu, mu_lo, mu_hi, _ = sq.estimate_mean(items)
        naive = sum(x for _, x, _ in items) / n if n else float("nan")
        rec = {
            "n": n,
            "ess": sq.effective_sample_size(items),
            "mean": mu,
            "mean_ci": (mu_lo, mu_hi),
            "naive_mean": naive,
            "quantiles": {},
            "quantile_cis": {},
        }
        for q in qs:
            rec["quantiles"][str(q)] = wd.quantile(q)
            rec["quantile_cis"][str(q)] = wd.quantile_ci(q)
        if bootstrap:
            rec["mean_ci_boot"] = sq.bootstrap_mean_ci(items, B=1000, seed=t)
            rec["q99_ci_boot"] = sq.bootstrap_quantile_ci(items, 0.99, B=1000, seed=t)
        recs.append(rec)
    return recs


def aggregate(recs, truth, qs):
    """跨试验聚合：偏差、RMSE、相对误差、区间覆盖率。"""
    out = {"trials": len(recs), "n_sample_mean": statistics.mean(r["n"] for r in recs)}
    out["ess_mean"] = statistics.mean(r["ess"] for r in recs)

    def summarize(ests, true_v):
        m = statistics.mean(ests)
        rmse = math.sqrt(statistics.mean((e - true_v) ** 2 for e in ests))
        return {
            "true": true_v,
            "est_mean": m,
            "rel_bias_pct": (m - true_v) / true_v * 100,
            "rmse": rmse,
            "rel_rmse_pct": rmse / true_v * 100,
        }

    out["mean"] = summarize([r["mean"] for r in recs], truth["mean"])
    out["naive_mean"] = summarize([r["naive_mean"] for r in recs], truth["mean"])
    hits = sum(1 for r in recs if r["mean_ci"][0] <= truth["mean"] <= r["mean_ci"][1])
    out["mean_ci_coverage"] = hits / len(recs)

    out["quantiles"] = {}
    for q in qs:
        sqs = str(q)
        tv = truth["quantiles"][sqs]
        s = summarize([r["quantiles"][sqs] for r in recs], tv)
        hits = sum(1 for r in recs
                   if r["quantile_cis"][sqs][0] <= tv <= r["quantile_cis"][sqs][1])
        s["ci_coverage"] = hits / len(recs)
        out["quantiles"][sqs] = s
    return out


def attribution_check(durs, sids, make_sampler):
    """单试验：按服务维度归因，对比估计总耗时占比与真实占比。"""
    sampler = make_sampler(0)
    for d, sid in zip(durs, sids):
        sampler.offer(d, sid)
    est = sq.groupby(sampler.items, lambda sid: SERVICES[sid])
    true_total = {}
    for d, sid in zip(durs, sids):
        true_total[SERVICES[sid]] = true_total.get(SERVICES[sid], 0.0) + d
    grand_true = sum(true_total.values())
    grand_est = sum(g["est_total_ms"] for g in est.values())
    rows = []
    for svc in SERVICES:
        rows.append({
            "service": svc,
            "true_share_pct": true_total[svc] / grand_true * 100,
            "est_share_pct": est[svc]["est_total_ms"] / grand_est * 100,
            "true_mean_ms": true_total[svc] / (len(durs) * SERVICE_W[SERVICES.index(svc)]),
            "est_mean_ms": est[svc]["est_mean_ms"],
            "n_sample": est[svc]["n_sample"],
        })
    return rows


def fmt_pct(x):
    return "%.2f%%" % x


def print_scenario(name, agg, qs):
    print("\n=== %s ===" % name)
    print("平均样本量 n=%.0f  平均 ESS=%.0f  试验次数=%d"
          % (agg["n_sample_mean"], agg["ess_mean"], agg["trials"]))
    print("%-8s %14s %14s %12s %12s %10s" %
          ("指标", "真值", "估计均值", "相对偏差", "相对RMSE", "CI覆盖率"))
    m = agg["mean"]
    print("%-8s %14.2f %14.2f %12s %12s %10s" %
          ("mean", m["true"], m["est_mean"], fmt_pct(m["rel_bias_pct"]),
           fmt_pct(m["rel_rmse_pct"]), fmt_pct(agg["mean_ci_coverage"] * 100)))
    nm = agg["naive_mean"]
    print("%-8s %14.2f %14.2f %12s %12s %10s" %
          ("naive", nm["true"], nm["est_mean"], fmt_pct(nm["rel_bias_pct"]),
           fmt_pct(nm["rel_rmse_pct"]), "-"))
    for q in qs:
        s = agg["quantiles"][str(q)]
        print("%-8s %14.2f %14.2f %12s %12s %10s" %
              ("p%g" % (q * 100), s["true"], s["est_mean"],
               fmt_pct(s["rel_bias_pct"]), fmt_pct(s["rel_rmse_pct"]),
               fmt_pct(s["ci_coverage"] * 100)))


def main():
    results = {}

    print("生成标准负载 N=%d ..." % N)
    durs_std, sids_std = gen_standard(N, seed=42)
    print("生成极端长尾负载 N=%d ..." % N)
    durs_ext, sids_ext = gen_extreme_tail(N, seed=43)

    truth_std = truth_stats(durs_std, QS)
    truth_ext = truth_stats(durs_ext, QS_TAIL)
    results["truth_standard"] = truth_std
    results["truth_extreme"] = truth_ext
    print("标准负载真值: mean=%.2fms  %s" %
          (truth_std["mean"],
           "  ".join("p%g=%.1f" % (q * 100, v) for q, v in
                     zip(QS, truth_std["quantiles"].values()))))
    print("长尾负载真值: mean=%.2fms  %s  max=%.0f" %
          (truth_ext["mean"],
           "  ".join("p%g=%.1f" % (q * 100, v) for q, v in
                     zip(QS_TAIL, truth_ext["quantiles"].values())),
           truth_ext["max"]))

    TRIALS = 100

    scenarios = [
        ("A 标准负载+均匀采样1%",
         durs_std, sids_std, truth_std, QS,
         lambda t: sq.UniformSampler(0.01, seed=1000 + t), TRIALS, False),
        ("B 标准负载+PPS耗时采样1%",
         durs_std, sids_std, truth_std, QS,
         lambda t: sq.PPSDurationSampler(0.01, seed=2000 + t), TRIALS, False),
        ("C 极端长尾+均匀采样1%",
         durs_ext, sids_ext, truth_ext, QS_TAIL,
         lambda t: sq.UniformSampler(0.01, seed=3000 + t), TRIALS, False),
        ("D 极端长尾+PPS耗时采样1%",
         durs_ext, sids_ext, truth_ext, QS_TAIL,
         lambda t: sq.PPSDurationSampler(0.01, seed=4000 + t), TRIALS, False),
        ("E 标准负载+均匀采样0.02%(小样本)",
         durs_std, sids_std, truth_std, QS,
         lambda t: sq.UniformSampler(0.0002, seed=5000 + t), 200, True),
    ]

    for name, durs, sids, truth, qs, factory, trials, boot in scenarios:
        print("运行场景 %s (%d 次试验) ..." % (name, trials))
        recs = run_trials(durs, sids, factory, qs, trials, bootstrap=boot)
        agg = aggregate(recs, truth, qs)
        print_scenario(name, agg, qs)
        entry = {"aggregate": agg}
        if boot:
            # 小样本场景：展示 3 个代表性试验的完整区间与告警
            reps = []
            for r in recs[:3]:
                warns = sq.reliability_warnings(r["n"], r["ess"], qs)
                reps.append({
                    "n": r["n"], "ess": round(r["ess"], 1),
                    "mean": r["mean"], "mean_ci_analytic": r["mean_ci"],
                    "mean_ci_bootstrap": r["mean_ci_boot"],
                    "p99": r["quantiles"]["0.99"],
                    "p99_ci_analytic": r["quantile_cis"]["0.99"],
                    "p99_ci_bootstrap": r["q99_ci_boot"],
                    "warnings": warns,
                })
                print("  代表试验 n=%d ESS=%.0f mean=%.1f CIa=[%.1f,%.1f] "
                      "CIb=[%.1f,%.1f] p99=%.1f p99CIb=[%.1f,%.1f]" % (
                          r["n"], r["ess"], r["mean"],
                          r["mean_ci"][0], r["mean_ci"][1],
                          r["mean_ci_boot"][0], r["mean_ci_boot"][1],
                          r["quantiles"]["0.99"],
                          r["q99_ci_boot"][0], r["q99_ci_boot"][1]))
                for w in warns:
                    print("    告警: %s" % w)
            entry["representative"] = reps
        results[name] = entry

    # 归因对比（场景 A/B 各一次试验）
    print("\n=== 归因对比：各服务总耗时占比（估计 vs 真实）===")
    for label, factory in [("均匀采样", lambda t: sq.UniformSampler(0.01, seed=777)),
                           ("PPS采样", lambda t: sq.PPSDurationSampler(0.01, seed=888))]:
        rows = attribution_check(durs_std, sids_std, factory)
        print("[%s]" % label)
        print("%-10s %14s %14s %14s %14s %8s" %
              ("service", "真实占比%", "估计占比%", "真实均值ms", "估计均值ms", "样本数"))
        for r in rows:
            print("%-10s %14.2f %14.2f %14.2f %14.2f %8d" % (
                r["service"], r["true_share_pct"], r["est_share_pct"],
                r["true_mean_ms"], r["est_mean_ms"], r["n_sample"]))
        results["attribution_" + label] = rows

    with open("results/experiments.json", "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print("\n结果已写入 results/experiments.json")


if __name__ == "__main__":
    main()
