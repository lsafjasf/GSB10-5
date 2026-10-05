"""Self tests and benchmarks for heightmap interpolation + normals.

Run:  python3 src/selftest.py
Only the Python 3 standard library is used.
"""

import gc
import math
import resource
import sys
import time
import tracemalloc

sys.path.insert(0, __file__.rsplit("/", 1)[0] or ".")
import heightmap as hm_mod
from heightmap import HeightMap, make_map, fill_missing, resample, compute_normals
from heightmap import normal_error_stats, validate_normals, NAN

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"[{PASS if ok else FAIL}] {name}" + (f" -- {detail}" if detail else ""))


EXTENT = 1024.0
REF_N = 1025
CLIFF_H = 6.0
CLIFF_W = 2.0
CLIFF_X = 512.0


def flat_fn(c, r):
    return 3.0


def slope_fn_factory(cell):
    a, b = 0.05, 0.03

    def fn(c, r):
        return a * c * cell + b * r * cell

    return fn, a, b


def cliff_fn_factory(cell):
    def fn(c, r):
        x = c * cell
        return CLIFF_H * math.tanh((x - CLIFF_X) / CLIFF_W)

    return fn


def cliff_band_mask(w, h, cell, half_width=8.0):
    mask = bytearray(w * h)
    for r in range(h):
        for c in range(w):
            if abs(c * cell - CLIFF_X) <= half_width:
                mask[r * w + c] = 1
    return mask


def section(title):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def test_flat():
    section("1. Flat terrain")
    cell = EXTENT / (65 - 1)
    m = make_map(65, 65, cell, flat_fn)
    n = compute_normals(m)
    rep = validate_normals(n)
    check("all normals finite", not rep["bad_finite"])
    check("all normals unit length", not rep["bad_length"],
          f"tol={rep['tol']}, points={rep['points']}")
    check("all normals point up", not rep["bad_side"])
    up = all(n[0][i] == 0.0 and n[1][i] == 0.0 and n[2][i] == 1.0
             for i in range(rep["points"]))
    check("every normal equals (0,0,1)", up, f"{rep['points']} points")


def test_slope():
    section("2. Gentle slope (bilinear should be exact)")
    ref_cell = EXTENT / (REF_N - 1)
    fn, a, b = slope_fn_factory(ref_cell)
    ref = compute_normals(make_map(REF_N, REF_N, ref_cell, fn))
    analytical = (-a, -b, 1.0)
    al = math.sqrt(sum(v * v for v in analytical))
    analytical = tuple(v / al for v in analytical)
    max_dev = 0.0
    for i in range(REF_N * REF_N):
        d = max(abs(ref[k][i] - analytical[k]) for k in range(3))
        max_dev = max(max_dev, d)
    check("reference normals match analytic normal", max_dev < 1e-6,
          f"max component deviation {max_dev:.2e}")
    low_n = 9
    low_cell = EXTENT / (low_n - 1)
    fn_low, _, _ = slope_fn_factory(low_cell)
    low = make_map(low_n, low_n, low_cell, fn_low)
    up = resample(low, REF_N, REF_N)
    stats = normal_error_stats(ref, compute_normals(up))
    check("9x9 -> bilinear -> 1025x1025 normal error ~ 0",
          stats["max_deg"] < 1e-3,
          f"max {stats['max_deg']:.3e} deg, mean {stats['mean_deg']:.3e} deg")


def test_cliff_resolution():
    section("3. Cliff: angle error vs resolution (reference = analytic 1025 grid)")
    ref_cell = EXTENT / (REF_N - 1)
    ref_map = make_map(REF_N, REF_N, ref_cell, cliff_fn_factory(ref_cell))
    ref_n = compute_normals(ref_map)
    band = cliff_band_mask(REF_N, REF_N, ref_cell)
    print(f"{'low-res N':>10} {'mean(deg)':>11} {'rms(deg)':>10} "
          f"{'max(deg)':>10} {'band mean':>11} {'band max':>10}")
    rows = []
    for low_n in (9, 17, 33, 65, 129, 257, 513):
        low_cell = EXTENT / (low_n - 1)
        low = make_map(low_n, low_n, low_cell, cliff_fn_factory(low_cell))
        up = resample(low, REF_N, REF_N)
        n = compute_normals(up)
        all_s = normal_error_stats(ref_n, n)
        band_s = normal_error_stats(ref_n, n, band)
        rows.append((low_n, all_s, band_s))
        print(f"{low_n:>10} {all_s['mean_deg']:>11.4f} {all_s['rms_deg']:>10.4f} "
              f"{all_s['max_deg']:>10.4f} {band_s['mean_deg']:>11.4f} "
              f"{band_s['max_deg']:>10.4f}")
    rep = validate_normals(n)
    check("all upsampled normals finite/unit/upward",
          not rep["bad_finite"] and not rep["bad_length"] and not rep["bad_side"])
    coarse_mean = rows[0][1]["mean_deg"]
    fine_mean = rows[-1][1]["mean_deg"]
    check("error shrinks with higher resolution", fine_mean < coarse_mean,
          f"mean {coarse_mean:.3f} -> {fine_mean:.3f} deg")
    check("error concentrates at the cliff (band mean > whole-map mean)",
          all(band_s["mean_deg"] > 2.0 * all_s["mean_deg"]
              for _, all_s, band_s in rows))
    worst = max(rows, key=lambda x: x[2]["mean_deg"])
    print(f"note: at N={worst[0]} cliff-band mean error "
          f"{worst[2]['mean_deg']:.2f} deg vs whole-map "
          f"{worst[1]['mean_deg']:.2f} deg -- bilinear turns the unresolved "
          f"step into a ramp, so the normal there is most wrong.")


def test_missing():
    section("4. Missing heights: deterministic fill and rejection rules")
    low_n = 65
    low_cell = EXTENT / (low_n - 1)

    def hole_fn(c, r):
        if (c * 7 + r * 13) % 23 == 0:
            return None
        return cliff_fn_factory(low_cell)(c, r)

    m = make_map(low_n, low_n, low_cell, hole_fn)
    n_missing = hm_mod.count_missing(m)
    check("missing samples detected", n_missing > 0, f"{n_missing} of {low_n*low_n}")
    up_raw = resample(m, REF_N, REF_N)
    nan_propagated = sum(1 for v in up_raw.z if math.isnan(v))
    check("NaN propagates through bilinear interpolation (never silently faked)",
          nan_propagated > 0, f"{nan_propagated} NaN output vertices")
    rejected = False
    try:
        compute_normals(up_raw)
    except ValueError:
        rejected = True
    check("compute_normals rejects NaN input", rejected)
    t0 = time.perf_counter()
    n_filled = fill_missing(m)
    t_fill = time.perf_counter() - t0
    check("fill_missing fills exactly the missing cells",
          n_filled == n_missing and hm_mod.count_missing(m) == 0,
          f"{n_filled} filled in {t_fill*1000:.2f} ms")
    up = resample(m, REF_N, REF_N)
    n = compute_normals(up)
    rep = validate_normals(n)
    check("normals after fill finite/unit/upward",
          not rep["bad_finite"] and not rep["bad_length"] and not rep["bad_side"],
          f"{rep['points']} points checked one by one")

    ref_cell = EXTENT / (REF_N - 1)
    ref_n = compute_normals(make_map(REF_N, REF_N, ref_cell,
                                     cliff_fn_factory(ref_cell)))
    stats = normal_error_stats(ref_n, n)
    band = cliff_band_mask(REF_N, REF_N, ref_cell)
    bstats = normal_error_stats(ref_n, n, band)
    print(f"missing-input pipeline error: mean {stats['mean_deg']:.4f} deg, "
          f"max {stats['max_deg']:.4f} deg; cliff band mean "
          f"{bstats['mean_deg']:.4f} deg")

    empty = make_map(4, 4, 1.0, lambda c, r: None)
    rejected_all = False
    try:
        fill_missing(empty)
    except ValueError:
        rejected_all = True
    check("all-missing grid is rejected (no information to fill from)",
          rejected_all)

    m2 = make_map(5, 5, 1.0,
                  lambda c, r: 10.0 if (c, r) == (0, 0) else None)
    fill_missing(m2)
    check("single-valid-cell fill is deterministic (all cells equal 10.0)",
          all(v == 10.0 for v in m2.z), f"{sum(1 for v in m2.z if v == 10.0)}/25")


def test_performance():
    section("5. Performance on 1,000,000 grid points (1000x1000)")
    W = H = 1000
    src_n = 500
    src_cell = EXTENT / (src_n - 1)
    src = make_map(src_n, src_n, src_cell, cliff_fn_factory(src_cell))
    gc.collect()
    tracemalloc.start()
    t0 = time.perf_counter()
    up = resample(src, W, H)
    t_res = time.perf_counter() - t0
    t0 = time.perf_counter()
    n = compute_normals(up)
    t_norm = time.perf_counter() - t0
    cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(f"points: {W*H:,}  (interpolated from {src_n}x{src_n})")
    print(f"bilinear resample : {t_res*1000:9.1f} ms")
    print(f"normal computation: {t_norm*1000:9.1f} ms")
    print(f"total             : {(t_res+t_norm)*1000:9.1f} ms")
    print(f"Python heap peak (tracemalloc): {peak/1e6:8.2f} MB")
    print(f"process peak RSS (ru_maxrss) : {rss_kb/1024:8.2f} MB")
    rep = validate_normals(n)
    check("1M-point normals finite/unit/upward",
          not rep["bad_finite"] and not rep["bad_length"] and not rep["bad_side"],
          f"{rep['points']:,} points")

    gc.collect()
    tracemalloc.start()
    t0 = time.perf_counter()
    direct = make_map(W, H, EXTENT / (W - 1),
                      cliff_fn_factory(EXTENT / (W - 1)))
    dn = compute_normals(direct)
    t_direct = time.perf_counter() - t0
    _, peak_direct = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"direct 1000x1000 build+normals : {t_direct*1000:9.1f} ms, "
          f"heap peak {peak_direct/1e6:.2f} MB")


def main():
    test_flat()
    test_slope()
    test_cliff_resolution()
    test_missing()
    test_performance()
    section("Summary")
    n_pass = sum(1 for _, ok in results if ok)
    print(f"{n_pass}/{len(results)} checks passed")
    if n_pass != len(results):
        for name, ok in results:
            if not ok:
                print("  FAILED:", name)
        sys.exit(1)


if __name__ == "__main__":
    main()
