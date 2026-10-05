"""slowquery —— 慢查询采样的加权还原估计库（仅依赖 Python 标准库）。

采样设计
--------
1. UniformSampler：均匀伯努利采样。每条记录独立以概率 p 入样，权重 w = 1/p。
2. PPSDurationSampler：按耗时大小的 PPS（probability proportional to size）泊松采样。
   入样概率 p(x) = min(1, x / tau)，tau 由先导样本（pilot）标定，使期望采样率等于
   目标采样率；权重 w = 1 / p(x)。耗时越长入样概率越大，超过 tau 的查询必然入样，
   因此长尾被确定性保留，同时权重还原保证估计无偏。

加权还原（Horvitz-Thompson 框架）
---------------------------------
设全量记录为 x_1..x_N，记录 i 的入样概率为 p_i（均匀采样 p_i = p；
PPS 采样 p_i = min(1, x_i/tau)），入样指示 I_i ~ Bernoulli(p_i)，权重 w_i = 1/p_i。

  总量估计  That = sum( w_i * x_i * I_i )
      E[That] = sum( x_i * E[I_i] / p_i ) = sum( x_i ) = T   —— 严格无偏。
  均值估计  mu_hat = sum(w_i x_i) / sum(w_i)   （比率估计，一致性无偏，偏差 O(1/n)）
  分布还原  Fhat(t) = sum( w_i * 1{x_i <= t} ) / sum(w_i)，
      对任意 t 有 E[Fhat(t)] = F(t)，分位数由 Fhat 反演（线性插值）。

注意：对 PPS 样本“直接算算术平均”等价于假设等权，是有偏的（系统性偏高，
因为慢查询被过采样），必须乘权重还原。
"""

import bisect
import math
import random

Z_95 = 1.959964


# ---------------------------------------------------------------------------
# 采样器
# ---------------------------------------------------------------------------

class UniformSampler:
    """均匀伯努利采样：每条记录以概率 rate 独立入样，权重恒为 1/rate。"""

    def __init__(self, rate, seed=None):
        if not 0 < rate <= 1:
            raise ValueError("rate 必须在 (0, 1] 内")
        self.rate = rate
        self.weight = 1.0 / rate
        self._rng = random.Random(seed)
        self.n_seen = 0
        self.n_kept = 0
        self.items = []  # (weight, value, attrs)

    def offer(self, duration_ms, attrs=None):
        self.n_seen += 1
        if self._rng.random() < self.rate:
            self.n_kept += 1
            item = (self.weight, duration_ms, attrs)
            self.items.append(item)
            return item
        return None


class PPSDurationSampler:
    """按耗时 PPS 采样：p(x) = min(1, x/tau)，tau 由先导样本标定。

    前 pilot_size 条记录作为 pilot 用于标定 tau（标定后 pilot 记录同样按
    p(x) 参与采样，不特殊对待），此后纯流式，内存 O(pilot_size + 样本量)。
    """

    def __init__(self, target_rate, seed=None, pilot_size=4096):
        if not 0 < target_rate <= 1:
            raise ValueError("target_rate 必须在 (0, 1] 内")
        self.target_rate = target_rate
        self.pilot_size = pilot_size
        self._rng = random.Random(seed)
        self._pilot = []
        self.tau = None
        self.n_seen = 0
        self.n_kept = 0
        self.items = []

    def _calibrate(self):
        xs = sorted(x for x, _ in self._pilot)
        lo, hi = xs[0] * 1e-3, xs[-1] * 1e3 + 1.0
        for _ in range(80):
            mid = 0.5 * (lo + hi)
            s = 0.0
            for x in xs:
                p = x / mid
                s += 1.0 if p >= 1.0 else p
            if s / len(xs) > self.target_rate:
                lo = mid
            else:
                hi = mid
        self.tau = 0.5 * (lo + hi)

    def _sample_one(self, duration_ms, attrs):
        x = duration_ms if duration_ms > 0 else 1e-9
        p = x / self.tau
        if p >= 1.0:
            prob, w = 1.0, 1.0
        else:
            prob, w = p, 1.0 / p
        if self._rng.random() < prob:
            self.n_kept += 1
            item = (w, duration_ms, attrs)
            self.items.append(item)
            return item
        return None

    def offer(self, duration_ms, attrs=None):
        self.n_seen += 1
        if self.tau is None:
            self._pilot.append((duration_ms, attrs))
            if len(self._pilot) >= self.pilot_size:
                self._calibrate()
                pilot, self._pilot = self._pilot, None
                for x, a in pilot:
                    self._sample_one(x, a)
            return None
        return self._sample_one(duration_ms, attrs)


# ---------------------------------------------------------------------------
# 加权还原估计
# ---------------------------------------------------------------------------

def _wx(items):
    for it in items:
        yield it[0], it[1]


def estimate_total(items):
    """HT 总量估计：sum(w*x)，严格无偏。"""
    return sum(w * x for w, x in _wx(items))


def estimate_mean(items):
    """比率均值估计 mu = sum(w*x)/sum(w)，返回 (mu, lo, hi, se)。

    方差按泊松采样下比率估计量的线性化方差：
        Var(mu) ~= sum( w_i^2 (x_i - mu)^2 ) / (sum w_i)^2
    """
    sw = swx = 0.0
    for w, x in _wx(items):
        sw += w
        swx += w * x
    if sw <= 0:
        return (float("nan"),) * 4
    mu = swx / sw
    var = 0.0
    for w, x in _wx(items):
        d = x - mu
        var += w * w * d * d
    var /= sw * sw
    se = math.sqrt(var)
    return mu, mu - Z_95 * se, mu + Z_95 * se, se


def effective_sample_size(items):
    """Kish 有效样本量 ESS = (sum w)^2 / sum(w^2)。权重越不均 ESS 越小。"""
    sw = sw2 = 0.0
    for w, _ in _wx(items):
        sw += w
        sw2 += w * w
    return sw * sw / sw2 if sw2 > 0 else 0.0


class WeightedDistribution:
    """加权经验分布：Fhat(t) = sum(w*1{x<=t}) / sum(w)，分位数为 Fhat 的
    线性插值反演（与全量数据单位权结果定义一致，可公平对比）。"""

    def __init__(self, items):
        pairs = sorted((x, w) for w, x in _wx(items))
        self.values = [x for x, _ in pairs]
        n = len(pairs)
        cum = [0.0] * n
        pos = [0.0] * n
        c = 0.0
        for i, (x, w) in enumerate(pairs):
            c += w
            cum[i] = c
            pos[i] = c - 0.5 * w
        self.cum = cum
        self.pos = pos
        self.total_weight = c
        self.n = n

    def cdf(self, t):
        i = bisect.bisect_right(self.values, t)
        return self.cum[i - 1] / self.total_weight if i else 0.0

    def quantile(self, q):
        if self.n == 0:
            return float("nan")
        target = q * self.total_weight
        pos, vals = self.pos, self.values
        if target <= pos[0]:
            return vals[0]
        if target >= pos[-1]:
            return vals[-1]
        i = bisect.bisect_right(pos, target)
        lo_p, hi_p = pos[i - 1], pos[i]
        lo_v, hi_v = vals[i - 1], vals[i]
        frac = (target - lo_p) / (hi_p - lo_p)
        return lo_v + frac * (hi_v - lo_v)

    def quantile_ci(self, q, z=Z_95):
        """Woodruff 置信区间：先给 Fhat(qe) 一个正态区间，再反演回分位数。

        se(Fhat) = sqrt( sum( w_i^2 (1{x_i<=qe} - q)^2 ) ) / sum(w)
        CI = [ Fhat^{-1}(q - z*se), Fhat^{-1}(q + z*se) ]
        """
        if self.n == 0:
            return (float("nan"), float("nan"))
        qe = self.quantile(q)
        sw = self.total_weight
        # 权重序列由 cum 差分得到，按是否 <= qe 分两段累加 w^2
        prev = 0.0
        s = 0.0
        for x, c in zip(self.values, self.cum):
            w = c - prev
            prev = c
            d = (1.0 if x <= qe else 0.0) - q
            s += w * w * d * d
        se = math.sqrt(s) / sw
        lo_q = q - z * se
        hi_q = q + z * se
        if lo_q < 0.0:
            lo_q = 0.0
        if hi_q > 1.0:
            hi_q = 1.0
        return self.quantile(lo_q), self.quantile(hi_q)


def exact_quantiles(sorted_values, qs):
    """全量精确分位数，与 WeightedDistribution(单位权).quantile 同一定义。"""
    n = len(sorted_values)
    out = []
    for q in qs:
        target = q * n
        if target <= 0.5:
            out.append(sorted_values[0])
        elif target >= n - 0.5:
            out.append(sorted_values[-1])
        else:
            lo = int(math.floor(target - 0.5))
            frac = target - 0.5 - lo
            out.append(sorted_values[lo] * (1 - frac) + sorted_values[lo + 1] * frac)
    return out


def reliability_warnings(n, ess, quantiles):
    """小样本/尾部分位数的可靠性告警。样本量不足时绝不只报一个数。"""
    warns = []
    if n < 30:
        warns.append(
            "样本量 n=%d < 30：任何点估计都不可靠，必须结合置信区间解读" % n)
    if ess < 30:
        warns.append(
            "有效样本量 ESS=%.1f < 30：权重过度集中，估计方差大，结果不可靠" % ess)
    for q in quantiles:
        support = n * min(q, 1.0 - q)
        if support < 10:
            warns.append(
                "分位数 %.4f 的期望支撑样本 %.1f < 10：该分位数估计不可靠，"
                "置信区间会极宽或退化" % (q, support))
    return warns


def groupby(items, key_fn):
    """按维度归因：对每组给出估计的查询数、总耗时、均值（均为 HT/比率估计）。"""
    groups = {}
    for w, x, attrs in items:
        k = key_fn(attrs)
        g = groups.get(k)
        if g is None:
            groups[k] = [w, w * x, 1]
        else:
            g[0] += w
            g[1] += w * x
            g[2] += 1
    return {
        k: {
            "est_count": sw,
            "est_total_ms": swx,
            "est_mean_ms": swx / sw,
            "n_sample": n,
        }
        for k, (sw, swx, n) in groups.items()
    }


def bootstrap_mean_ci(items, B=1000, seed=None):
    """百分位 bootstrap 均值置信区间（小样本时的备用手段）。"""
    rng = random.Random(seed)
    n = len(items)
    if n == 0:
        return (float("nan"), float("nan"))
    ests = []
    for _ in range(B):
        sw = swx = 0.0
        for _ in range(n):
            w, x = items[rng.randrange(n)][:2]
            sw += w
            swx += w * x
        ests.append(swx / sw)
    ests.sort()
    return ests[int(0.025 * B)], ests[min(int(0.975 * B), B - 1)]


def bootstrap_quantile_ci(items, q, B=1000, seed=None):
    """百分位 bootstrap 分位数置信区间。"""
    rng = random.Random(seed)
    n = len(items)
    if n == 0:
        return (float("nan"), float("nan"))
    ests = []
    for _ in range(B):
        resample = [items[rng.randrange(n)] for _ in range(n)]
        ests.append(WeightedDistribution(resample).quantile(q))
    ests.sort()
    return ests[int(0.025 * B)], ests[min(int(0.975 * B), B - 1)]
