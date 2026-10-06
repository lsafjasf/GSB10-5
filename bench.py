"""Benchmark: reverse-lookup latency & index memory at 100,000 artifacts."""
import json
import os
import random
import tempfile
import time

import provenance as pv

N = 100_000
LOOKUPS = 100_000


def make_record(i):
    content = f"artifact-{i}".encode()
    record = {
        "schema": pv.SCHEMA,
        "artifact": {
            "name": f"app-{i}.tar.gz",
            "sha256": pv.sha256_bytes(content),
            "size": len(content),
        },
        "source": {
            "vcs": "git",
            "repo": "https://example.invalid/team/app.git",
            "commit": f"{i % 1000:040x}",
            "tree_sha256": pv.sha256_bytes(f"tree-{i // 10}".encode()),
            "dirty": False,
        },
        "build": {
            "command": "make",
            "params": {"OPT": str(i % 4)},
            "builder": "ci",
            "finished_at": f"2026-10-06T00:{(i // 60) % 60:02d}:{i % 60:02d}+00:00",
            "nonce": f"{i:032x}",
        },
        "toolchain": {"python": "3.12.3", "os": "Linux", "machine": "x86_64"},
        "dependencies": {
            "lockfile_sha256": "a" * 64,
            "resolved": [
                {"name": "requests", "version": "2.31.0", "digest": "r" * 64},
                {"name": "urllib3", "version": "2.2.1", "digest": "u" * 64},
            ],
        },
    }
    return pv.finalize_record(record)


def main():
    tmp = tempfile.mkdtemp(prefix="prov-bench-")
    ledger = os.path.join(tmp, "ledger.jsonl")

    t0 = time.perf_counter()
    records = [make_record(i) for i in range(N)]
    with open(ledger, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(pv.canonical_json(rec).decode("ascii") + "\n")
    write_s = time.perf_counter() - t0
    ledger_mb = os.path.getsize(ledger) / 1e6

    t0 = time.perf_counter()
    idx = pv.Index.build(ledger)
    build_s = time.perf_counter() - t0

    ids = [r["artifact"]["sha256"] for r in records]
    rng = random.Random(42)
    sample = [rng.choice(ids) for _ in range(LOOKUPS)]

    # plain reverse lookup: artifact id -> record (disk seek + json parse)
    t0 = time.perf_counter()
    lat_us = []
    for aid in sample:
        t1 = time.perf_counter()
        hit = idx.lookup_artifact(aid)
        lat_us.append((time.perf_counter() - t1) * 1e6)
        assert hit and hit[0]["source"]["commit"]
    lookup_s = time.perf_counter() - t0
    lat_us.sort()
    p50 = lat_us[len(lat_us) // 2]
    p99 = lat_us[int(len(lat_us) * 0.99)]

    # full verified trace: lookup + integrity recomputation
    t0 = time.perf_counter()
    for aid in sample:
        node = idx.trace(aid)[0]
        assert node["integrity_ok"]
    trace_s = time.perf_counter() - t0

    out = {
        "records": N,
        "ledger_size_mb": round(ledger_mb, 2),
        "ledger_write_s": round(write_s, 3),
        "index_build_s": round(build_s, 3),
        "index_memory_bytes": idx.memory_bytes(),
        "index_memory_mb": round(idx.memory_bytes() / 1e6, 2),
        "lookups": LOOKUPS,
        "lookup_total_s": round(lookup_s, 3),
        "lookup_avg_us": round(lookup_s / LOOKUPS * 1e6, 2),
        "lookup_p50_us": round(p50, 2),
        "lookup_p99_us": round(p99, 2),
        "trace_total_s": round(trace_s, 3),
        "trace_avg_us": round(trace_s / LOOKUPS * 1e6, 2),
        "lookups_per_s": round(LOOKUPS / lookup_s, 0),
    }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
