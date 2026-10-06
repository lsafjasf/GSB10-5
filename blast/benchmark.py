"""规模化基准：耗时与内存峰值（1 万服务 / 10 万依赖）。"""

from __future__ import annotations

import gc
import resource
import time
import tracemalloc

from .assessment import assess
from .topology import generate_large_topology


def run_benchmark(n_services: int = 10_000, n_edges: int = 100_000,
                  fault_domain: str | None = None) -> dict:
    gc.collect()
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    t0 = time.perf_counter()
    tracemalloc.start()
    topo = generate_large_topology(n_services=n_services, n_edges=n_edges)
    gen_ms = (time.perf_counter() - t0) * 1000
    _, gen_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    if fault_domain is None:
        fault_domain = next(iter(topo.services.values())).fail_domain

    t1 = time.perf_counter()
    tracemalloc.start()
    result = assess(topo, {fault_domain})
    assess_ms = (time.perf_counter() - t1) * 1000
    _, assess_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "n_services": len(topo.services),
        "n_dependency_edges": topo.provider_count(),
        "fault_domain": fault_domain,
        "generate_ms": round(gen_ms, 1),
        "assess_ms": round(assess_ms, 1),
        "total_ms": round(gen_ms + assess_ms, 1),
        "generate_peak_mib": round(gen_peak / 1024 / 1024, 2),
        "assess_peak_mib": round(assess_peak / 1024 / 1024, 2),
        "process_maxrss_mib": round(max(rss_after, rss_before) / 1024, 2),
        "result": result.summary(),
    }
