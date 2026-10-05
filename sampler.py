"""慢查询采样与加权还原库（仅使用 Python 标准库）。

采样侧（设计-based）：
    每条记录以预先确定的包含概率 p(v) 独立入样（Bernoulli 采样）。
    - UniformSampler:  p(v) = rate                          （均匀采样）
    - DurationSampler: p(v) = min(1, rate * max(1, v / T))  （按耗时过采样慢查询）

还原侧（Horvitz-Thompson 加权）：
    样本权重 w = 1 / p(v)。对总体任意总量 Y = sum(y_i)，
    估计量 Y_hat = sum_{i in S} w_i * y_i 满足
        E[Y_hat] = sum_i y_i * (1/p_i) * P(i in S) = sum_i y_i = Y
    即对任意已知包含概率的采样设计，加权还原是**无偏**的。
    均值用 Hajek 比率形式（sum(w*v)/sum(w)），分位数用加权 ECDF 的逆函数，
    二者都是一致估计（样本量增大时依概率收敛到真值）。

置信区间：
    - 均值：正态近似，se = sqrt(加权方差 / ESS)。
    - 分位数：Woodruff 方法——先对 F(x) 在 q 处构造 CI
      [q - z*se, q + z*se]，se = sqrt(q(1-q)/ESS)，再对加权 ECDF 求逆。
    - ESS 为 Kish 有效样本量：(sum w)^2 / sum(w^2)。
    - ESS < MIN_ESS，或估计 q 分位数时 ESS*(1-q) < MIN_TAIL_ESS，
      判定为不可靠（此时仍给出 CI，但调用方必须展示"不可靠"标记）。
"""
from __future__ import annotations

import bisect
import math
import random
from statistics import NormalDist

_NORM = NormalDist()

#: 有效样本量低于该值时，任何点估计都标记为不可靠
MIN_ESS = 30.0
#: 估计 q 分位数时，要求 ESS*(1-q) 不低于该值（尾部有效样本太少则不可靠）
MIN_TAIL_ESS = 10.0


def _z(confidence: float) -> float:
    return _NORM.inv_cdf(0.5 + confidence / 2.0)


class WeightedSample:
    """存放 (value, weight) 样本并提供加权还原统计量。"""

    def __init__(self) -> None:
        self._vw: list[tuple[float, float]] = []
        self._cache = None

    def add(self, value: float, weight: float) -> None:
        if weight <= 0:
            raise ValueError("weight must be positive")
        self._vw.append((float(value), float(weight)))
        self._cache = None

    def __len__(self) -> int:
        return len(self._vw)

    # ---- 基本量 ----

    @property
    def total_weight(self) -> float:
        """总体规模的 HT 无偏估计（还原出的总记录数）。"""
        return math.fsum(w for _, w in self._vw)

    @property
    def effective_sample_size(self) -> float:
        """Kish 有效样本量 ESS = (sum w)^2 / sum(w^2)。"""
        tw = 0.0
        tw2 = 0.0
        for _, w in self._vw:
            tw += w
            tw2 += w * w
        return tw * tw / tw2 if tw2 else 0.0

    def reliable(self) -> bool:
        return len(self._vw) >= 2 and self.effective_sample_size >= MIN_ESS

    def quantile_reliable(self, q: float) -> bool:
        return self.reliable() and self.effective_sample_size * (1.0 - q) >= MIN_TAIL_ESS

    # ---- 加权 ECDF ----

    def _sorted(self):
        if self._cache is None:
            pts = sorted(self._vw)
            cum = []
            c = 0.0
            for _, w in pts:
                c += w
                cum.append(c)
            self._cache = (pts, cum)
        return self._cache

    def quantile(self, q: float) -> float:
        """加权还原的 q 分位数（加权 ECDF 的广义逆）。"""
        if not 0.0 <= q <= 1.0:
            raise ValueError("q out of range")
        pts, cum = self._sorted()
        if not pts:
            return float("nan")
        i = bisect.bisect_left(cum, q * cum[-1])
        return pts[min(i, len(pts) - 1)][0]

    # ---- 均值 ----

    def mean(self) -> float:
        tw = self.total_weight
        if not tw:
            return float("nan")
        return math.fsum(v * w for v, w in self._vw) / tw

    def mean_ci(self, confidence: float = 0.95) -> tuple[float, float]:
        m = self.mean()
        ess = self.effective_sample_size
        if ess <= 0:
            return (float("nan"), float("nan"))
        tw = self.total_weight
        var = math.fsum(w * (v - m) ** 2 for v, w in self._vw) / tw
        half = _z(confidence) * math.sqrt(var / ess)
        return (m - half, m + half)

    # ---- 分位数置信区间（Woodruff） ----

    def quantile_ci(self, q: float, confidence: float = 0.95) -> tuple[float, float]:
        ess = self.effective_sample_size
        if ess <= 0:
            return (float("nan"), float("nan"))
        se = math.sqrt(q * (1.0 - q) / ess)
        half = _z(confidence) * se
        lo = max(0.0, q - half)
        hi = min(1.0, q + half)
        return (self.quantile(lo), self.quantile(hi))

    # ---- 归因：慢查询对总耗时的贡献 ----

    def total_estimate(self) -> float:
        """总体总耗时的 HT 无偏估计。"""
        return math.fsum(v * w for v, w in self._vw)

    def tail_share(self, q: float) -> float:
        """最慢 (1-q) 部分的查询占总耗时的加权比例（用于慢查询归因）。"""
        pts, _ = self._sorted()
        if not pts:
            return float("nan")
        thr = self.quantile(q)
        total = math.fsum(v * w for v, w in pts)
        if total <= 0:
            return float("nan")
        tail = math.fsum(v * w for v, w in pts if v >= thr)
        return tail / total


class UniformSampler:
    """均匀 Bernoulli 采样：每条记录以固定概率 rate 入样，权重 1/rate。"""

    def __init__(self, rate: float, seed: int | None = None) -> None:
        if not 0.0 < rate <= 1.0:
            raise ValueError("rate must be in (0, 1]")
        self.rate = rate
        self._rng = random.Random(seed)

    def inclusion_prob(self, value: float) -> float:
        return self.rate

    def maybe_add(self, value: float, sample: WeightedSample) -> bool:
        if self._rng.random() < self.rate:
            sample.add(value, 1.0 / self.rate)
            return True
        return False


class DurationSampler:
    """按耗时采样：p(v) = min(1, rate * max(1, v / threshold))。

    快于 threshold 的查询以基准概率 rate 入样；越慢的查询包含概率越大，
    极端慢查询必定入样（p=1）。权重仍为 1/p(v)，保证还原无偏，
    同时长尾部分的有效样本量远大于均匀采样。
    """

    def __init__(self, rate: float, threshold: float, seed: int | None = None) -> None:
        if not 0.0 < rate <= 1.0:
            raise ValueError("rate must be in (0, 1]")
        if threshold <= 0:
            raise ValueError("threshold must be positive")
        self.rate = rate
        self.threshold = threshold
        self._rng = random.Random(seed)

    def inclusion_prob(self, value: float) -> float:
        return min(1.0, self.rate * max(1.0, value / self.threshold))

    def maybe_add(self, value: float, sample: WeightedSample) -> bool:
        p = self.inclusion_prob(value)
        if self._rng.random() < p:
            sample.add(value, 1.0 / p)
            return True
        return False
