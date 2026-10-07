"""Bounded-memory sketches used to observe label cardinality.

Only the Python standard library is used.

Structures:

* HyperLogLog -- approximate distinct-count with O(2**p) bytes of memory.
* SpaceSaving -- Metwally et al. top-K heavy-hitter sketch, O(K) memory.
* LabelSketch -- per-label state machine:

    - exact phase: a plain set of values while distinct <= exact_limit;
    - sketch phase: once the limit is exceeded the exact set is dropped and
      only HLL registers, per-window HLLs, a SpaceSaving heavy-hitter table
      and a small reservoir sample are kept.

The promotion from exact to sketch is what keeps memory bounded when a label
such as order_id takes millions of distinct values: the full value set is
never materialised.
"""

from __future__ import annotations

import math

__all__ = ["HyperLogLog", "SpaceSaving", "LabelSketch", "MAX_WINDOWS", "hash64"]

MAX_WINDOWS = 128

_GOLDEN = 0x9E3779B97F4A7C15


def hash64(value) -> int:
    """Fast 64-bit hash.  Process-local seed is acceptable for sketches."""
    if isinstance(value, str):
        h = hash(value)
    elif isinstance(value, int):
        h = value
    else:
        h = hash(repr(value))
    h = (h ^ _GOLDEN) & 0xFFFFFFFFFFFFFFFF
    return h


def _xorshift64(state: int) -> int:
    state &= 0xFFFFFFFFFFFFFFFF
    state ^= (state << 13) & 0xFFFFFFFFFFFFFFFF
    state ^= state >> 7
    state ^= (state << 17) & 0xFFFFFFFFFFFFFFFF
    return state & 0xFFFFFFFFFFFFFFFF


class HyperLogLog:
    """HyperLogLog distinct-count sketch (Flajolet et al., 2007)."""

    __slots__ = ("p", "m", "registers")

    def __init__(self, p: int = 12):
        if not 4 <= p <= 16:
            raise ValueError("p must be in [4, 16]")
        self.p = p
        self.m = 1 << p
        self.registers = bytearray(self.m)

    def add(self, value) -> None:
        self.add_hash(hash64(value))

    def add_hash(self, h: int) -> None:
        idx = h & (self.m - 1)
        w = h >> self.p
        rank = (64 - self.p) - w.bit_length() + 1 if w else (64 - self.p) + 1
        if rank > self.registers[idx]:
            self.registers[idx] = rank

    def merge(self, other: "HyperLogLog") -> None:
        if self.p != other.p:
            raise ValueError("cannot merge HLLs with different precision")
        a, b = self.registers, other.registers
        for i in range(self.m):
            if b[i] > a[i]:
                a[i] = b[i]

    def estimate(self) -> float:
        m = self.m
        if m == 16:
            alpha = 0.673
        elif m == 32:
            alpha = 0.697
        elif m == 64:
            alpha = 0.709
        else:
            alpha = 0.7213 / (1 + 1.079 / m)
        z = 0.0
        zeros = 0
        for r in self.registers:
            z += 2.0 ** -r
            if r == 0:
                zeros += 1
        est = alpha * m * m / z
        if est <= 2.5 * m and zeros:
            est = m * math.log(m / zeros)
        return est


class SpaceSaving:
    """Space-Saving heavy-hitter sketch (Metwally, Agrawal, El Abbadi).

    Keeps at most k counters; reported counts overestimate true frequency by
    at most the current minimum counter.  min-frequency items are found with
    a short-circuit scan over at most k counters (on id-like labels every
    insertion evicts a minimum entry, and low counts dominate, so the scan
    stops almost immediately).
    """

    __slots__ = ("k", "counts", "min_count")

    def __init__(self, k: int = 64):
        self.k = k
        self.counts = {}
        self.min_count = 1

    def add(self, value) -> None:
        counts = self.counts
        c = counts.get(value)
        if c is not None:
            nc = c + 1
            counts[value] = nc
            if c == self.min_count:
                self.min_count = min(counts.values())
            return
        if len(counts) < self.k:
            counts[value] = 1
            if self.min_count > 1:
                self.min_count = 1
            return
        m = self.min_count
        for key, cnt in counts.items():
            if cnt == m:
                del counts[key]
                break
        counts[value] = m + 1
        self.min_count = m + 1

    def top(self, n: int) -> list:
        """Return up to n (value, count) pairs, highest count first."""
        return sorted(self.counts.items(), key=lambda kv: -kv[1])[:n]


class LabelSketch:
    """Per-label cardinality observer with bounded memory."""

    __slots__ = (
        "name", "exact_limit", "topk", "reservoir_size",
        "total", "exact", "hll", "window_exact", "window_hll",
        "heavy", "reservoir", "_rng_state", "_cur_window", "_cur_window_hll",
    )

    def __init__(self, name: str, exact_limit: int = 256, p: int = 12,
                 topk: int = 64, reservoir_size: int = 512, seed: int = 0xC0FFEE):
        self.name = name
        self.exact_limit = exact_limit
        self.topk = topk
        self.reservoir_size = reservoir_size
        self.total = 0
        self.exact = set()
        self.hll = HyperLogLog(p)
        self.window_exact = {}
        self.window_hll = {}
        self.heavy = SpaceSaving(topk)
        self.reservoir = []
        self._rng_state = (seed & 0xFFFFFFFFFFFFFFFF) or 1
        self._cur_window = -1
        self._cur_window_hll = None

    def add(self, value: str, window: int = 0, h: int | None = None) -> None:
        self.total += 1
        if h is None:
            h = hash64(value)
        self.hll.add_hash(h)
        self.heavy.add(value)
        self._sample(value)
        if self.exact is not None:
            self.exact.add(value)
            ws = self.window_exact.get(window)
            if ws is None:
                ws = self.window_exact[window] = set()
            ws.add(value)
            if len(self.exact) > self.exact_limit:
                self._promote()
        else:
            if window == self._cur_window:
                wh = self._cur_window_hll
            else:
                wh = self.window_hll.get(window)
                if wh is None:
                    wh = self.window_hll[window] = HyperLogLog(self.hll.p)
                    self._cap_windows()
                self._cur_window = window
                self._cur_window_hll = wh
            wh.add_hash(h)

    def _sample(self, value: str) -> None:
        r = self.reservoir
        if len(r) < self.reservoir_size:
            r.append(value)
        elif (self.total & 7) == 0:
            # reservoir over the 1/8 systematic subsample (still uniform)
            self._rng_state = _xorshift64(self._rng_state)
            j = self._rng_state % max(1, self.total >> 3)
            if j < self.reservoir_size:
                r[j] = value

    def _promote(self) -> None:
        """Drop exact sets; convert per-window sets to HLLs."""
        for w, s in self.window_exact.items():
            wh = HyperLogLog(self.hll.p)
            for v in s:
                wh.add(v)
            self.window_hll[w] = wh
        self.window_exact = None
        self.exact = None
        self._cap_windows()

    def _cap_windows(self) -> None:
        while len(self.window_hll) > MAX_WINDOWS:
            oldest = min(self.window_hll)
            victim = self.window_hll.pop(oldest)
            nxt = min(self.window_hll)
            self.window_hll[nxt].merge(victim)

    @property
    def is_exact(self) -> bool:
        return self.exact is not None

    def distinct(self) -> float:
        if self.exact is not None:
            return float(len(self.exact))
        return self.hll.estimate()

    def top_values(self, n: int) -> list:
        return self.heavy.top(n)

    def window_stats(self, window_totals: dict) -> dict:
        """Per-window new-distinct counts and (new / total series) ratios."""
        out = {}
        if self.window_exact is not None:
            for w, s in sorted(self.window_exact.items()):
                t = window_totals.get(w, 0)
                out[w] = {"new_distinct": len(s),
                          "ratio": (len(s) / t) if t else 0.0}
        else:
            for w in sorted(self.window_hll):
                est = self.window_hll[w].estimate()
                t = window_totals.get(w, 0)
                out[w] = {"ratio": min(1.0, est / t) if t else 0.0,
                          "new_distinct": est}
        return out
