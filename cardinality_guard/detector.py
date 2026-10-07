"""High-cardinality label identification.

Identification evidence (three required dimensions plus combination risk):

1. value_count      -- number of distinct values
                       (exact below exact_limit, HyperLogLog above);
2. growth_rate      -- distinct values appearing *new in a window* divided by
                       the number of series in that window, averaged over the
                       most recent windows.  A bounded dimension (region,
                       status) trends toward ~0 over time; an unbounded one
                       (order_id, trace_id) keeps producing fresh values in
                       every window and stays near 1;
3. distinctiveness  -- selectivity = distinct/total (fraction of values
                       that are unique) and normalised entropy of value
                       frequencies.  Uniform spread with selectivity ~1
                       means the label is effectively an id; a few dominant
                       values are truncatable.

Combination risk is evaluated separately on label *pairs*.  Pair joint
sketches are fed with a 1/r subsample of the stream (r=4) for speed; the
realisation factor is measured inside that sub-stream as
``joint_s / min(sampled_observations, da*db)``.  When the factor is near 1
the pair is fully crossed -- the labels vary independently and their
combination explodes multiplicatively.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .sketch import LabelSketch, HyperLogLog, hash64

_MASK64 = (1 << 64) - 1
_SALT_GOLDEN = 0x9E3779B97F4A7C15

__all__ = ["Thresholds", "LabelReport", "DetectionReport", "CardinalityDetector"]


@dataclass
class Thresholds:
    """Decision thresholds (documented in README)."""
    high_value_count: int = 1000
    high_growth_rate: float = 0.25
    high_selectivity: float = 0.5
    sustained_growth_windows: int = 3
    combo_independence: float = 0.8
    combo_product_floor: int = 4096


@dataclass
class LabelReport:
    name: str
    total: int
    distinct: float
    selectivity: float
    entropy_norm: float
    growth_rate: float
    growth_windows: int
    is_high_cardinality: bool
    is_sustained_growth: bool
    reasons: list
    top_values: list
    is_exact: bool


@dataclass
class DetectionReport:
    total_series: int
    distinct_series: float = 0.0
    labels: dict = field(default_factory=dict)
    high_cardinality_labels: list = field(default_factory=list)
    exploding_pairs: list = field(default_factory=list)
    combination_product: float = 0.0


class CardinalityDetector:
    """Observe series and classify labels as low / high cardinality."""

    def __init__(self, thresholds: Thresholds | None = None,
                 exact_limit: int = 256, reservoir_size: int = 512,
                 track_pairs: bool = True, seed: int = 0xBADC0DE):
        self.thresholds = thresholds or Thresholds()
        self.exact_limit = exact_limit
        self.reservoir_size = reservoir_size
        self.track_pairs = track_pairs
        self.sketches: dict[str, LabelSketch] = {}
        self.total_series = 0
        self.window_totals: dict[int, int] = {}
        self._total_hll = HyperLogLog(14)
        self._pair_hlls: dict[tuple, HyperLogLog] = {}
        self._pair_salts: dict[str, int] = {}
        self._snapshots: list[tuple] = []
        self._snap_seen = 0
        self._pair_sample_rate = 4  # pair sketches see 1/r of the stream
        self._pair_samples = 0
        self._seed = seed

    def feed(self, labels: dict, window: int = 0) -> None:
        """Ingest one time series (a mapping of label name -> value)."""
        if not isinstance(labels, dict):
            raise TypeError("labels must be a dict[str, str]")
        self.total_series += 1
        self.window_totals[window] = self.window_totals.get(window, 0) + 1
        keys = sorted(labels)
        items = [(k, str(labels[k])) for k in keys]

        sketches = self.sketches
        pair_salts = self._pair_salts
        pair_hlls = self._pair_hlls
        exact_limit = self.exact_limit
        reservoir_size = self.reservoir_size
        seed = self._seed
        hashes = []
        salts = []
        total_acc = 0
        for name, value in items:
            h = hash(value) & _MASK64
            hashes.append(h)
            sk = sketches.get(name)
            if sk is None:
                sk = LabelSketch(
                    name, exact_limit=exact_limit,
                    reservoir_size=reservoir_size,
                    seed=(seed ^ hash64(name)) & 0xFFFFFFFF or 1)
                sketches[name] = sk
            sk.add(value, window, h)
            sa = pair_salts.get(name)
            if sa is None:
                sa = ((hash64(name) * _SALT_GOLDEN) & _MASK64) | 1
                pair_salts[name] = sa
            salts.append(sa)
            total_acc = (total_acc + sa * h) & _MASK64
        self._total_hll.add_hash(total_acc)

        # bounded reservoir of whole series, used to backfill pair sketches
        # for labels that were first seen after stream start.
        self._snap_seen += 1
        snapshots = self._snapshots
        if len(snapshots) < reservoir_size:
            snapshots.append(tuple(items))
        else:
            if hash(self._snap_seen) % self._snap_seen < reservoir_size:
                snapshots[self._snap_seen % reservoir_size] = tuple(items)

        if self.track_pairs:
            sampled = (self.total_series % self._pair_sample_rate) == 0
            if sampled:
                self._pair_samples += 1
            n = len(keys)
            for i in range(n):
                for j in range(i + 1, n):
                    key = (keys[i], keys[j])
                    hll = pair_hlls.get(key)
                    if hll is None:
                        hll = HyperLogLog(11)
                        sa0 = pair_salts[keys[i]]
                        sb0 = pair_salts[keys[j]]
                        for snap in self._snapshots:
                            sd = dict(snap)
                            va = sd.get(keys[i])
                            vb = sd.get(keys[j])
                            if va is not None and vb is not None:
                                ph = ((sa0 * (hash(va) & _MASK64))
                                      ^ (sb0 * (hash(vb) & _MASK64))) & _MASK64
                                hll.add_hash(ph)
                        pair_hlls[key] = hll
                    if sampled:
                        ph = ((salts[i] * hashes[i])
                              ^ (salts[j] * hashes[j])) & _MASK64
                        hll.add_hash(ph)

    # ------------------------------------------------------------- analysis
    def _entropy_norm(self, sk: LabelSketch, distinct: float) -> float:
        """Normalised entropy (0..1) estimated from the reservoir sample."""
        sample = sk.reservoir
        if len(sample) < 2:
            return 0.0
        freq = {}
        for v in sample:
            freq[v] = freq.get(v, 0) + 1
        n = len(sample)
        h = 0.0
        for c in freq.values():
            p = c / n
            h -= p * math.log2(p)
        return h / math.log2(min(distinct, n)) if min(distinct, n) > 1 else 0.0

    def _growth(self, sk: LabelSketch) -> tuple[float, int]:
        stats = sk.window_stats(self.window_totals)
        if len(stats) < 2:
            return 0.0, len(stats)
        recent = sorted(stats)[-self.thresholds.sustained_growth_windows:]
        ratios = [stats[w]["ratio"] for w in recent]
        return min(1.0, sum(ratios) / len(ratios)), len(recent)

    def report(self) -> DetectionReport:
        th = self.thresholds
        rep = DetectionReport(total_series=self.total_series)
        rep.distinct_series = self._total_hll.estimate()
        product = 1.0
        for name, sk in sorted(self.sketches.items()):
            distinct = sk.distinct()
            product *= min(distinct, self.total_series)
            selectivity = min(1.0, distinct / sk.total) if sk.total else 0.0
            entropy = self._entropy_norm(sk, distinct)
            growth, gwindows = self._growth(sk)
            sustained = (
                gwindows >= th.sustained_growth_windows
                and growth >= th.high_growth_rate
            )
            reasons = []
            if distinct >= th.high_value_count:
                reasons.append(f"取值数量={distinct:,.0f} >= {th.high_value_count}")
            if sustained:
                reasons.append(
                    f"近{gwindows}窗口新增/总量={growth:.3f} >= {th.high_growth_rate}（持续增长）")
            if selectivity >= th.high_selectivity and distinct >= th.high_value_count:
                reasons.append(
                    f"区分度: 选择率={selectivity:.3f} >= {th.high_selectivity}，"
                    f"归一化熵={entropy:.3f}")
            is_hc = bool(reasons)
            lr = LabelReport(
                name=name, total=sk.total, distinct=distinct,
                selectivity=selectivity, entropy_norm=entropy,
                growth_rate=growth, growth_windows=gwindows,
                is_high_cardinality=is_hc, is_sustained_growth=sustained,
                reasons=reasons,
                top_values=sk.top_values(10), is_exact=sk.is_exact)
            rep.labels[name] = lr
            if is_hc:
                rep.high_cardinality_labels.append(name)

        rep.combination_product = min(product, float(self.total_series))
        rep.exploding_pairs = self._exploding_pairs()
        return rep

    def _exploding_pairs(self) -> list:
        """Realisation factor measured inside the 1/r sampled sub-stream.

        factor = joint_s / min(sampled_observations, da*db): the fraction of
        combinations realisable within the sampled stream that were actually
        realised.  Near 1 means the pair is fully crossed (explosive).
        """
        out = []
        t = max(1, self._pair_samples)
        for (a, b), hll in sorted(self._pair_hlls.items()):
            joint_s = hll.estimate()
            joint = min(self.total_series, joint_s * self._pair_sample_rate)
            da = self.sketches[a].distinct()
            db = self.sketches[b].distinct()
            if da < 2 or db < 2:
                continue
            ceiling = min(t, da * db)
            factor = min(1.0, joint_s / ceiling) if ceiling else 0.0
            if (factor >= self.thresholds.combo_independence
                    and da * db >= self.thresholds.combo_product_floor
                    and joint >= 64):
                out.append({
                    "pair": (a, b), "joint": joint,
                    "marginal_product": da * db,
                    "ceiling": ceiling, "factor": factor,
                })
        out.sort(key=lambda x: (-x["factor"], -x["marginal_product"]))
        return out[:50]
