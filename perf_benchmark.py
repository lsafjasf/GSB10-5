"""Benchmark: classify 100,000 events, report wall time and peak memory.

Run: python3 perf_benchmark.py [n]
"""

import random
import sys
import time
import tracemalloc

from seir import Event, Incident, Level
from seir.incident import sla_report
from seir.scoring import Signal

CATEGORIES = ["malware", "phishing", "anomaly", "policy", "scan", "dos"]


def make_events(n, seed=42):
    rng = random.Random(seed)
    events = []
    for i in range(n):
        k = rng.choice((1, 1, 1, 2, 2, 3, 4))  # 1-4 signals per event
        signals = [
            Signal(
                id=f"S{i}-{j}",
                category=rng.choice(CATEGORIES),
                strength=rng.uniform(0, 95),
                confidence=rng.uniform(0.3, 1.0),
            )
            for j in range(k)
        ]
        events.append(
            Event(
                id=f"EVT-{i}",
                asset_tier=rng.randint(1, 5),
                affected=rng.randint(1, 5000),
                signals=signals,
            )
        )
    return events


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100_000
    clock = lambda: 1_700_000_000.0  # injected, fixed time

    tracemalloc.start()
    t0 = time.perf_counter()

    events = make_events(n)
    t1 = time.perf_counter()

    incidents = []
    for ev in events:
        inc = Incident(ev.id, clock)
        inc.update(ev)
        incidents.append(inc)
    t2 = time.perf_counter()

    report = sla_report(incidents)
    t3 = time.perf_counter()

    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    dist = {level: sum(1 for i in incidents if i.level == level) for level in Level}
    print(f"events:                {n:,}")
    print(f"generation time:       {t1 - t0:.3f}s")
    print(f"classification time:   {t2 - t1:.3f}s  "
          f"({n / (t2 - t1):,.0f} events/s, {(t2 - t1) / n * 1e6:.2f} us/event)")
    print(f"sla report time:       {t3 - t2:.3f}s")
    print(f"peak memory (tracemalloc): {peak / 1024 / 1024:.1f} MiB")
    print("level distribution:    "
          + ", ".join(f"{lv.name}={dist[lv]:,}" for lv in Level))
    print("sla breaches:          "
          + ", ".join(f"{lv.name}={report[lv].breached:,}" for lv in Level))


if __name__ == "__main__":
    main()
