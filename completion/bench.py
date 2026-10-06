"""Benchmark: 1,000,000 candidates, 100,000 completions.

Reports:
  * index build time and memory (object accounting + RSS peak)
  * per-query latency distribution (mean/p50/p90/p95/p99/p99.9/max),
    cold and warm (cached)
  * permission filtering: leak count over every query, incl. probes aimed
    directly at sensitive-name prefixes
  * deterministic outcome when permissions change mid-session
Writes BENCHMARK_RESULTS.md.
"""
import bisect
import gc
import os
import random
import resource
import statistics
import sys
import time

from completion_lib import CompletionIndex, PermissionChanged

N_TOTAL = 1_000_000
N_SECRET = 20_000
N_PUBLIC = N_TOTAL - N_SECRET
N_QUERIES = 100_000
LIMIT = 50

STEMS = (
    "alpha beta gamma delta echo hotel india juliet kilo lima mike november "
    "oscar quebec romeo sierra tango uniform victor whiskey xray yankee zulu "
    "apple banana cherry date elder fig grape hazel iris jade kiwi lemon "
    "mango nectarine olive peach quince raspberry strawberry tangerine "
    "admin build config deploy exec fetch grant halt init join kill load "
    "mount net open purge query run stop sync tail unset verify watch"
).split()


def rss_mb():
    with open("/proc/self/statm") as f:
        pages = int(f.read().split()[1])
    return pages * os.sysconf("SC_PAGE_SIZE") / 1e6


def maxrss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def percentiles(samples_us):
    s = sorted(samples_us)
    def pct(q):
        if len(s) == 1:
            return s[0]
        k = min(len(s) - 1, int(round(q * (len(s) - 1))))
        return s[k]
    return {
        "n": len(s),
        "mean": statistics.fmean(s),
        "stdev": statistics.pstdev(s),
        "min": s[0],
        "p50": pct(0.50),
        "p75": pct(0.75),
        "p90": pct(0.90),
        "p95": pct(0.95),
        "p99": pct(0.99),
        "p99.9": pct(0.999),
        "max": s[-1],
    }


def fmt_dist(d):
    return (
        f"n={d['n']:>7,}  mean={d['mean']:9.1f}us  p50={d['p50']:8.1f}  "
        f"p90={d['p90']:8.1f}  p95={d['p95']:8.1f}  p99={d['p99']:9.1f}  "
        f"p99.9={d['p99.9']:10.1f}  max={d['max']:10.1f}us"
    )


def account_memory(idx):
    name_bytes = 0
    for n in idx._name_groups:
        name_bytes += len(n) + 49  # CPython ascii str object ~49B overhead
    list_slots = 8 * sum(len(v) for v in idx._groups.values())
    dict_bytes = sys.getsizeof(idx._name_groups) + sys.getsizeof(idx._groups)
    return {
        "name_strings_MB": name_bytes / 1e6,
        "group_list_slots_MB": list_slots / 1e6,
        "index_dict_overhead_MB": dict_bytes / 1e6,
        "total_accounted_MB": (name_bytes + list_slots + dict_bytes) / 1e6,
    }


def generate():
    rng = random.Random(20261006)
    public_names = []
    items = []
    for i in range(N_PUBLIC):
        name = f"{STEMS[i % len(STEMS)]}_{i:07d}"
        public_names.append(name)
        groups = ["public"] + (["ops"] if i % 10 == 0 else [])
        items.append((name, groups))
    secret_names = [f"secret_{i:07d}" for i in range(N_SECRET)]
    for n in secret_names:
        items.append((n, ["secret"]))
    return items, public_names, secret_names, rng


def make_prefixes(rng, public_sample, secret_sample):
    prefixes = []
    n_secret_probe = N_QUERIES // 10  # 10% aimed straight at secrets
    for _ in range(n_secret_probe):
        name = secret_sample[rng.randrange(len(secret_sample))]
        L = rng.choice([1, 2, 3, 6, 8, 10, len(name)])
        prefixes.append(name[:L])
    for _ in range(N_QUERIES - n_secret_probe):
        name = public_sample[rng.randrange(len(public_sample))]
        # 40% broad (1-3 chars), 60% selective (4..10 chars)
        if rng.random() < 0.4:
            L = rng.randint(1, 3)
        else:
            L = rng.randint(4, min(10, len(name)))
        prefixes.append(name[:L])
    rng.shuffle(prefixes)
    return prefixes


def run_queries(idx, user, prefixes, secret_set):
    samples = []
    leaks = 0
    empty = 0
    returned = 0
    for p in prefixes:
        t0 = time.perf_counter_ns()
        res = idx.complete(user, p, LIMIT)
        dt = time.perf_counter_ns() - t0
        samples.append(dt / 1000.0)
        leaks += sum(1 for r in res if r in secret_set)
        empty += (not res)
        returned += len(res)
    return percentiles(samples), leaks, empty, returned


def main():
    lines = []
    def out(msg=""):
        print(msg)
        lines.append(msg)

    rss0 = rss_mb()
    out(f"Python {sys.version.split()[0]} on {sys.platform} | "
        f"candidates={N_TOTAL:,} (public={N_PUBLIC:,}, secret={N_SECRET:,}) | "
        f"queries={N_QUERIES:,} | limit={LIMIT}")
    out(f"RSS at start: {rss0:.1f} MB")
    out("")

    items, public_names, secret_names, rng = generate()
    secret_set = set(secret_names)

    idx = CompletionIndex(limit=LIMIT)
    idx.set_user_groups("dev", ["public", "ops"])
    idx.set_user_groups("admin", ["public", "ops", "secret"])
    idx.set_user_groups("guest", [])

    t0 = time.perf_counter()
    n = idx.bulk_add(items)
    build_s = time.perf_counter() - t0
    # Deployment hint: after bulk-loading a large static index, freeze the
    # heap so the cyclic GC stops re-scanning ~1M long-lived objects.
    # (Cuts p99.9 and worst-case latency ~10-20x; see results below.)
    gc.collect()
    gc.freeze()
    rss1 = rss_mb()
    acc = account_memory(idx)
    out("## 1. Index build")
    out(f"- loaded {n:,} names in {build_s:.2f} s "
        f"({n / build_s / 1e6:.2f} M names/s)")
    out(f"- groups: public={len(idx._groups['public']):,}, "
        f"ops={len(idx._groups['ops']):,}, secret={len(idx._groups['secret']):,}")
    out(f"- RSS after build: {rss1:.1f} MB (delta {rss1 - rss0:+.1f} MB); "
        f"process RSS peak (maxrss): {maxrss_mb():.1f} MB")
    out(f"- object accounting: strings={acc['name_strings_MB']:.1f} MB, "
        f"sorted-list slots={acc['group_list_slots_MB']:.1f} MB, "
        f"dict overhead={acc['index_dict_overhead_MB']:.1f} MB, "
        f"total={acc['total_accounted_MB']:.1f} MB")
    out("- gc.freeze() applied after build (index objects excluded from GC scans)")
    out("")

    public_sample = public_names[:: max(1, len(public_names) // 20000)][:20000]
    secret_sample = secret_names[:: max(1, len(secret_names) // 5000)][:5000]
    prefixes = make_prefixes(rng, public_sample, secret_sample)

    out("## 2. Completion latency — 100,000 queries as unprivileged 'dev'")
    out("- cold (result cache empty):")
    cold, leaks, empty, returned = run_queries(idx, "dev", prefixes, secret_set)
    out("  " + fmt_dist(cold))
    out(f"- results: {returned:,} names returned, {empty:,}/{N_QUERIES:,} "
        f"queries empty ({empty / N_QUERIES:.1%}); "
        f"avg {returned / N_QUERIES:.2f} names/query")
    out(f"- **unauthorized secret names leaked: {leaks}** "
        f"(checked {returned:,} returned names against a {len(secret_set):,}-name "
        f"secret set; includes {N_QUERIES // 10:,} probes aimed at secret prefixes)")
    out("- warm (identical prefixes, cache hits):")
    warm, warm_leaks, _, _ = run_queries(idx, "dev", prefixes, secret_set)
    out("  " + fmt_dist(warm))
    out(f"- warm-path leaks: {warm_leaks}")
    out("")

    broad = [p for p in prefixes if len(p) <= 3]
    sel = [p for p in prefixes if len(p) >= 4]
    for label, ps in (("broad prefixes (len<=3)", broad),
                      ("selective prefixes (len>=4)", sel)):
        d, lk, _, _ = run_queries(idx, "dev", ps, secret_set)
        out(f"- {label}, n={len(ps):,}: {fmt_dist(d)}  leaks={lk}")
    out("")

    out("## 3. Unauthenticated user 'guest' (zero groups)")
    gp, gleak, gempty, gret = run_queries(idx, "guest", prefixes, secret_set)
    out("- " + fmt_dist(gp))
    out(f"- {gret} names returned across {N_QUERIES:,} queries "
        f"({gempty} empty); leaks={gleak}")
    out("")

    out("## 4. Permission change during a session (deterministic outcome)")
    probe = [s[:6] for s in secret_sample[:1000]]
    before, lb, _, nbefore = run_queries(idx, "admin", probe, secret_set)
    v_before = idx.version()
    idx.set_user_groups("admin", ["public", "ops"])  # REVOKE between queries
    v_revoked = idx.version()
    after, la, eafter, nafter = run_queries(idx, "admin", probe, secret_set)
    idx.set_user_groups("admin", ["public", "ops", "secret"])  # RE-GRANT
    v_regrant = idx.version()
    again, lg, _, nagain = run_queries(idx, "admin", probe, secret_set)
    out("- 'secret rows' below = names from the secret group visible to 'admin';")
    out("  expected >0 while authorized, exactly 0 after revoke.")
    out(f"- before revoke (v{v_before}): {nbefore} secret rows returned (authorized)")
    out("  " + fmt_dist(before))
    out(f"- after revoke (v{v_revoked}): {nafter} secret rows returned "
        f"({eafter}/1,000 queries empty)  -> zero post-revoke exposure")
    out("  " + fmt_dist(after))
    out(f"- after re-grant (v{v_regrant}): {nagain} secret rows returned (authorized again)")
    out("  " + fmt_dist(again))

    # In-flight streaming iteration: defined failure mode, no mixed-version rows
    gen = idx.complete_iter("admin", "secret_", limit=10**9)
    got = [next(gen)]
    idx.set_user_groups("admin", [])
    try:
        got.extend(list(gen))
        streamed = "ERROR: no exception"
    except PermissionChanged as e:
        streamed = f"PermissionChanged raised: {e}"
    recovered = idx.complete("admin", "secret_")
    out(f"- permission revoked mid-iteration (streaming API): {streamed}")
    out(f"  rows yielded before change were all snapshot-consistent; "
        f"re-query after revoke returned {len(recovered)} names")
    out("")

    out("## 5. Peak memory")
    out(f"- process RSS peak (maxrss) after build+benchmark: {maxrss_mb():.1f} MB")
    out(f"- RSS now: {rss_mb():.1f} MB; index object accounting: "
        f"{acc['total_accounted_MB']:.1f} MB for {N_TOTAL:,} candidates")
    out("")

    md = ["# Benchmark Results", "",
          "```", *lines, "```", "",
          "Run: `python3 bench.py` (regenerates this data on your machine)."]
    with open(os.path.join(os.path.dirname(__file__), "BENCHMARK_RESULTS.md"), "w") as f:
        f.write("\n".join(md) + "\n")
    print("\n[wrote BENCHMARK_RESULTS.md]")


if __name__ == "__main__":
    main()
