"""End-to-end report: fit weights, validate, sensitivity, benchmark.

Run from the repository root:

    python3 examples/run_report.py
"""
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from risk_score.benchmark import run_benchmark
from risk_score.factors import (FACTOR_NAMES, FACTORS, fit_ecdf,
                                normalize_vector, raw_factor_vector)
from risk_score.model import (fit_logistic, risk_score, rollback_probability,
                              score_contributions)
from risk_score.sensitivity import monte_carlo_sensitivity, oat_sensitivity
from risk_score.synthetic import TRUE_WEIGHTS, generate_history
from risk_score.validation import (auc_fast, calibration, choose_threshold,
                                   point_biserial, risk_bands, spearman)

REPORTS = Path(__file__).resolve().parent.parent / "reports"
REPORTS.mkdir(exist_ok=True)

lines: list[str] = []


def out(s: str = "") -> None:
    print(s)
    lines.append(s)


def main() -> None:
    out("=" * 78)
    out("CHANGE RISK SCORING - EVIDENCE REPORT")
    out("=" * 78)

    # --- 1. Fit weights on labelled history -------------------------------
    t0 = time.perf_counter()
    metrics, X, y, ecdf = generate_history(20_000, seed=42)
    model = fit_logistic(X, y)
    out(f"\n[1] Weight fitting (logistic regression on {len(y)} labelled "
        f"historical changes)")
    out(f"    converged={model.converged} in {model.iterations} iterations, "
        f"fit time={time.perf_counter()-t0:.2f}s")
    out(f"    base rollback rate = {sum(y)/len(y):.3f}")
    out(f"    intercept b0 = {model.intercept:+.3f}")
    out(f"    {'factor':<24}{'fitted weight':>14}{'+/- SE':>10}"
        f"{'generator true':>16}")
    for name, w, se, tw in zip(FACTOR_NAMES, model.weights, model.std_errors,
                               TRUE_WEIGHTS):
        out(f"    {name:<24}{w*100:>13.1f}%{se:>10.3f}{tw*100:>15.1f}%")

    # --- 2. Contribution decomposition ------------------------------------
    rng_idx = 7
    sample = metrics[rng_idx]
    xv = X[rng_idx]
    out("\n[2] Contribution decomposition (example change)")
    out(f"    change #{rng_idx}: +{sample.lines_added}/-{sample.lines_deleted} "
        f"lines, {len(sample.files)} files, "
        f"reviewers={sample.reviewer_count}, emergency={sample.is_emergency}")
    out(f"    risk score = {risk_score(xv, model.weights):.1f}/100   "
        f"predicted P(rollback) = {rollback_probability(xv, model):.3f}")
    out(f"    {'factor':<24}{'value(0-1)':>12}{'weight':>9}{'points':>9}"
        f"{'share':>9}")
    for name, v, w, share in score_contributions(xv, model.weights):
        out(f"    {name:<24}{v:>12.3f}{w*100:>8.1f}%{w*v*100:>9.2f}"
            f"{share*100:>8.1f}%")

    # --- 3. Validation against actual outcomes ----------------------------
    scores = [risk_score(x, model.weights) for x in X]
    probs = [rollback_probability(x, model) for x in X]
    out("\n[3] Validation against actual outcomes (rollback labels)")
    out(f"    Spearman rank corr(score, rollback)   = {spearman(scores, y):+.3f}")
    out(f"    point-biserial corr(score, rollback)  = {point_biserial(scores, y):+.3f}")
    full_auc = auc_fast(scores, y)
    size_auc = auc_fast([x[0] for x in X], y)
    out(f"    ROC AUC, full 7-factor model          = {full_auc:.3f}")
    out(f"    ROC AUC, size-only baseline           = {size_auc:.3f}")
    cal = calibration(probs, y)
    out(f"    mean predicted P(rollback)            = {cal.mean_predicted:.3f}")
    out(f"    observed rollback rate                = {cal.observed_rate:.3f}")
    out(f"    Brier score                           = {cal.brier:.4f}")
    out("    calibration by predicted-risk decile "
        "(predicted | observed | n):")
    for mp, obs, _, n in cal.decile_table:
        out(f"      {mp:7.3f} | {obs:7.3f} | {n:5d}")
    threshold = choose_threshold(scores, y, cal.observed_rate)
    bands = risk_bands(scores, y, edges=(threshold * 0.6, threshold, threshold * 1.3))
    out(f"\n    score bands (high-risk threshold = {threshold:.1f}):")
    out(f"    {'band':<18}{'n':>7}{'rollback rate':>16}{'share of rollbacks':>20}")
    for b in bands:
        out(f"    {b.score_band:<18}{b.n:>7}{b.rollback_rate:>15.1%}"
            f"{b.share_of_rollbacks:>19.1%}")

    # --- 4. Sensitivity ----------------------------------------------------
    sens_X, sens_y = X[:2000], y[:2000]
    out("\n[4] Weight sensitivity")
    out("    (a) one factor +/-10% (difference redistributed to others)")
    out(f"    {'factor':<24}{'delta':>7}{'mean |dscore|':>14}"
        f"{'rank corr':>11}{'% reclassified':>17}")
    oat = oat_sensitivity(sens_X, model.weights, threshold)
    for r in oat:
        out(f"    {r.factor:<24}{r.delta_frac*100:>6.0f}%"
            f"{r.mean_abs_score_change:>14.3f}{r.spearman:>11.4f}"
            f"{r.fraction_reclassified*100:>16.2f}%")
    mc, per_change = monte_carlo_sensitivity(
        sens_X, model.weights, model.std_errors, threshold, draws=300, seed=7,
        beta=model.beta)
    out("    (b) Monte Carlo: beta_j ~ Gamma(mean=fitted, sd=fitted SE)")
    out(f"        draws                         = {mc.draws}")
    out(f"        score mean +/- std (all chg.) = {mc.score_mean:.2f} +/- "
        f"{mc.score_std:.2f}  (5-95 pct: {mc.score_p05:.2f} - {mc.score_p95:.2f})")
    out(f"        min rank correlation vs base  = {mc.rank_corr_min:.4f}")
    out(f"        high-risk verdict flip rate   = {mc.highrisk_flip_rate*100:.2f}%")
    # borderline cases specifically
    base = [risk_score(x, model.weights) for x in sens_X]
    near = [(i, b) for i, b in enumerate(base) if abs(b - threshold) <= 2.0]
    if near:
        flips_near = sum(
            1 for i, _ in near
            for s in per_change[i]
            if (s >= threshold) != (base[i] >= threshold))
        out(f"        borderline changes (+/-2 pts around threshold): "
            f"{len(near)}, verdict flip rate = "
            f"{flips_near / (mc.draws * len(near)) * 100:.1f}%")
    out("    Conclusion: ranking stable if min rank corr ~1 and flip rate low;")
    out("    borderline verdicts are expected to be less stable and are flagged.")

    # --- 5. Benchmark 100k changes ----------------------------------------
    out("\n[5] Benchmark: 100,000 changes scored")
    bench_metrics, _, _, bench_ecdf = generate_history(100_000, seed=5)
    res = run_benchmark(bench_metrics, bench_ecdf, model.weights,
                        keep_results=True)
    out(f"    total wall time      = {res['seconds']:.3f} s")
    out(f"    per change           = {res['per_change_us']:.2f} us")
    out(f"    throughput           = {res['n']/res['seconds']:,.0f} changes/s")
    out(f"    peak RSS (process)   = {res['peak_rss_mb']:.1f} MB")
    # streaming mode: don't retain scores
    res2 = run_benchmark(bench_metrics, bench_ecdf, model.weights,
                         keep_results=False)
    out(f"    peak RSS (streaming) = {res2['peak_rss_mb']:.1f} MB")
    out("    (peak RSS is whole-process via getrusage; streaming mode shows")
    out("     the cost without retaining the 100k result list)")

    # --- 6. Required edge scenarios ---------------------------------------
    out("\n[6] Mandated edge scenarios (see tests/test_risk_score.py)")
    from tests_fixtures import build_scenarios
    for label, m in build_scenarios():
        x = normalize_vector(raw_factor_vector(m), ecdf)
        s = risk_score(x, model.weights)
        p = rollback_probability(x, model)
        parts = score_contributions(x, model.weights)
        top = max(parts, key=lambda q: q[3])
        out(f"    {label:<36} score={s:6.1f}  P(rb)={p:.3f}  "
            f"dominant={top[0]} ({top[3]*100:.0f}%)")

    out("\n" + "=" * 78)
    out("Run tests:  python3 -m unittest discover -s tests -v")
    out("=" * 78)
    (REPORTS / "report.txt").write_text("\n".join(lines))
    print(f"\n[written to {REPORTS / 'report.txt'}]")


if __name__ == "__main__":
    main()
