"""Synthetic time-series generators for the four required scenarios.

Each generator yields (window, labels) tuples.  Low-cardinality coarse
dimensions (service/env/version/region/status/path) come from a small
correlated pool that is cycled, so in the low-cardinality case every
series reuses an existing tuple; in high-cardinality scenarios unique id
labels are attached to the same tuples (which mirrors real traffic: many
unique requests over a small set of endpoints/services).
"""

from __future__ import annotations

import math
import random

__all__ = ["generate", "SCENARIOS", "build_coarse_pool"]

SCENARIOS = ("low", "single", "combo", "growth")

_RESOURCES = (
    "users", "orders", "payments", "items", "carts", "sessions",
    "invoices", "shipments", "reviews", "searches",
)
_STATUSES = (("200", 0.980), ("500", 0.015), ("404", 0.005))


def _weighted_status(rng: random.Random) -> str:
    r = rng.random()
    acc = 0.0
    for status, w in _STATUSES:
        acc += w
        if r <= acc:
            return status
    return _STATUSES[0][0]


def build_coarse_pool(size, *, services=12, envs=4, versions=8,
                      regions=16, paths=50, seed=42):
    """Build a pool of correlated coarse label tuples."""
    rng = random.Random(seed)
    pool = []
    for i in range(size):
        svc = f"svc-{rng.randrange(services):02d}"
        env = ("prod", "prod", "prod", "stage", "dev")[rng.randrange(5)]
        ver = f"v{rng.randrange(versions) + 1}"
        reg = f"region-{rng.randrange(regions):02d}"
        status = _weighted_status(rng)
        res = _RESOURCES[rng.randrange(len(_RESOURCES))]
        path = f"/api/v{rng.randrange(2) + 1}/{res}"
        pool.append((svc, env, ver, reg, status,
                     path if paths > 0 else None))
    return pool


def _uuid(rng: random.Random) -> str:
    x = rng.getrandbits(128)
    h = f"{x:032x}"
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def generate(n: int, *, scenario: str = "combo", n_windows: int = 8,
             seed: int = 42, pool_size: int | None = None,
             pods: int = 20000):
    """Yield (window, {label: value}) for ``n`` synthetic series."""
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}; choose from {SCENARIOS}")
    rng = random.Random(seed)
    if pool_size is None:
        pool_size = min(800, max(1, n // 25))
    pool = build_coarse_pool(pool_size, seed=seed ^ 0x5EED)
    channels = ("EU", "US", "ASIA")
    per_window = max(1, n // n_windows)

    keys = ("service", "env", "version", "region", "status", "path")
    for i in range(n):
        window = min(n_windows - 1, i // per_window)
        base = pool[i % len(pool)]
        labels = dict(zip(keys, base))
        if labels["path"] is None:
            del labels["path"]

        if scenario == "single":
            labels["order_id"] = f"ORD-{i:08d}"

        elif scenario == "growth":
            # ids are globally unique per window -> every window sees
            # essentially 100% new distinct values (sustained growth).
            labels["order_id"] = f"ORD-{i:08d}"

        elif scenario == "combo":
            labels["trace_id"] = _uuid(rng)
            labels["order_id"] = f"ORD-{rng.choice(channels)}-{i:08d}"
            # path embeds the order: dynamic segment per request
            labels["path"] = f"{labels['path']}/{i}"
            # skewed pod assignment (power law): few hot pods, long tail
            idx = int(pods * (1 - (1 - rng.random()) ** 3))
            labels["pod"] = f"pod-{min(idx, pods - 1):05d}"

        yield window, labels
