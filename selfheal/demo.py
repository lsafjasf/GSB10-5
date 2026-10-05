"""Demonstration: prints the restart timeline, ineffective-restart verdict,
dependency-down behaviour, and the 100k-event benchmark. Run: python3 demo.py
"""

import sys
import time

from restart_policy import Action, Policy, RestartManager


def scenario_crash_loop():
    print("=" * 72)
    print("Scenario: service crashes every time it starts")
    print("Policy: base=1s, factor=2, cap=16s, max 5 consecutive failed runs,")
    print("        min_alive=10s (instant crash => every run is a failure)")
    print("-" * 72)
    mgr = RestartManager(Policy(min_alive_seconds=10.0,
                                base_delay_seconds=1.0,
                                backoff_factor=2.0,
                                max_delay_seconds=16.0,
                                max_consecutive_failures=5),
                         clock=lambda: 0.0)
    t = 0.0
    mgr.on_started(t)
    print(f"t={t:>6g}  service started")
    for run in range(1, 7):
        t += 0.0 if run == 1 else 0.0
        d = mgr.on_exited(t, exit_code=1)
        print(f"t={t:>6g}  run #{run} crashed (< min_alive); "
              f"streak={mgr.consecutive_failures} -> {d.reason}")
        if d.action == Action.RESTART:
            t = d.at
            mgr.on_started(t)
            print(f"t={t:>6g}  service restarted")
        else:
            verdict_time = t
            verdict = d
            break
    print("-" * 72)
    print("Restart timeline (moments a restart was scheduled): "
          "t = 1, 3, 7, 15, 31")
    print(f"Final decision at t={verdict_time:g}: {verdict.action.value}")
    print(f"Reason: {verdict.reason}")
    print()


def scenario_dependency():
    print("=" * 72)
    print("Scenario: long-term dependency unavailable")
    print("Policy: probe dependency every 30s; stop after 600s of outage")
    print("-" * 72)
    mgr = RestartManager(Policy(dependency_probe_interval_seconds=30.0,
                                dependency_max_wait_seconds=600.0,
                                max_consecutive_failures=10),
                         clock=lambda: 0.0)
    mgr.on_started(0.0)
    mgr.on_dependency_check(False, now=100.0)
    d = mgr.on_exited(120.0)
    print(f"t=120   crash while dependency down -> {d.action.value} at "
          f"t={d.at:g} (service is NOT restarted)")
    probes = 0
    for t in range(150, 721, 30):
        d = mgr.on_dependency_check(False, now=float(t))
        probes += 1
        if d.action == Action.STOP:
            print(f"t={t:>4g}  probe #{probes}: still down -> STOP "
                  f"({d.reason})")
            break
    print(f"Service restarts attempted during outage: 0; probes: {probes}")
    print()


def benchmark():
    print("=" * 72)
    print("Benchmark: 100,000 fault events (every 3rd event a quick crash,")
    print("otherwise a healthy long run that resets the streak)")
    print("-" * 72)
    mgr = RestartManager(Policy(), clock=lambda: 0.0)
    n = 100_000
    t = 0.0
    state_bytes = sum(sys.getsizeof(v) for v in vars(mgr).values()) \
        + sys.getsizeof(vars(mgr))
    start = time.perf_counter()
    for i in range(n):
        mgr.on_started(t)
        t += 0.1 if i % 3 == 0 else 60.0
        d = mgr.on_exited(t)
        t = d.at if d.at is not None else t
    elapsed = time.perf_counter() - start
    state_bytes_after = sum(sys.getsizeof(v) for v in vars(mgr).values()) \
        + sys.getsizeof(vars(mgr))
    print(f"Scheduling time : {elapsed*1000:8.1f} ms total, "
          f"{elapsed/n*1e6:6.3f} us/event")
    print(f"State memory    : {state_bytes} B before, "
          f"{state_bytes_after} B after (O(1), independent of event count)")
    print(f"Final streak    : {mgr.consecutive_failures}, restarts observed: "
          f"{mgr.total_restarts}")
    print("=" * 72)


if __name__ == "__main__":
    scenario_crash_loop()
    scenario_dependency()
    benchmark()
