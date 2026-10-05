from __future__ import annotations

import argparse
import gc
import resource
import time
import tracemalloc

from session_expiry import ManualClock, SessionManager


def run_benchmark(operations: int, session_count: int, trace_python: bool) -> None:
    if operations <= 0:
        raise ValueError("operations must be positive")
    if session_count <= 0 or session_count > operations:
        raise ValueError("session_count must be in the range 1..operations")

    gc.collect()
    if trace_python:
        tracemalloc.start()
    clock = ManualClock()
    manager = SessionManager(idle_timeout=3600, absolute_timeout=7200, clock=clock)

    creates = touches = gets = 0
    started = time.perf_counter()
    for index in range(operations):
        if index < session_count:
            manager.create(f"session-{index}")
            creates += 1
        else:
            session_id = f"session-{index % session_count}"
            if index % 10 == 0:
                manager.get(session_id)
                gets += 1
            else:
                manager.touch(session_id)
                touches += 1
        clock.advance(0.001)
    elapsed = time.perf_counter() - started
    traced_peak = 0
    if trace_python:
        _, traced_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

    peak_rss_mib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(f"operations={operations} create={creates} touch={touches} get={gets}")
    print(f"elapsed_seconds={elapsed:.3f}")
    print(f"operations_per_second={operations / elapsed:.0f}")
    print(f"peak_rss_mib={peak_rss_mib:.3f}")
    if trace_python:
        print(f"tracemalloc_peak_mib={traced_peak / 1024 / 1024:.3f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--operations", type=int, default=1_000_000)
    parser.add_argument("--sessions", type=int, default=10_000)
    parser.add_argument("--tracemalloc", action="store_true")
    args = parser.parse_args()
    run_benchmark(args.operations, args.sessions, args.tracemalloc)
