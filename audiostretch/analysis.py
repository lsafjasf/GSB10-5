"""测量工具：基频估计、咔哒声检测、时长/能量测量。"""
import math
from array import array
from operator import mul


def estimate_f0(samples, sr, fmin=60.0, fmax=1200.0, start=None, length=4096):
    """归一化自相关 + 抛物线插值估计基频（Hz）。返回 (f0, 置信度)。"""
    n = len(samples)
    if start is None:
        start = max(0, (n - length) // 2)
    end = min(n, start + length)
    seg = samples[start:end]
    m = len(seg)
    if m < 256:
        return 0.0, 0.0
    mean = sum(seg) / m
    x = [v - mean for v in seg]
    lag_min = max(1, int(sr / fmax))
    lag_max = min(m // 2, int(sr / fmin))
    vals = [0.0] * (lag_max + 2)
    for lag in range(lag_min, lag_max + 1):
        L = m - lag
        xs = x[:L]
        ys = x[lag:lag + L]
        e1 = sum(map(mul, xs, xs))
        e2 = sum(map(mul, ys, ys))
        if e1 <= 1e-12 or e2 <= 1e-12:
            v = 0.0
        else:
            v = sum(map(mul, xs, ys)) / math.sqrt(e1 * e2)
        vals[lag] = v
    best_v = max(vals[lag_min:lag_max + 1])
    if best_v <= 0.0:
        return 0.0, 0.0
    # 选第一个达到全局峰值 90% 的局部极大，避免把纯周期信号的
    # 二倍/三倍周期误判为基频周期。
    best_lag = 0
    for lag in range(lag_min, lag_max + 1):
        if vals[lag] >= 0.9 * best_v and vals[lag] >= vals[lag - 1] \
                and vals[lag] >= vals[lag + 1]:
            best_lag = lag
            break
    if best_lag == 0:
        best_lag = max(range(lag_min, lag_max + 1), key=lambda L: vals[L])
    lo, mid, hi = vals[best_lag - 1], vals[best_lag], vals[best_lag + 1]
    denom = lo - 2.0 * mid + hi
    frac = 0.5 * (lo - hi) / denom if abs(denom) > 1e-12 else 0.0
    if abs(frac) > 1.0:
        frac = 0.0
    lag_f = best_lag + frac
    return sr / lag_f, best_v


def first_diffs(samples):
    n = len(samples)
    return array('f', (abs(samples[i] - samples[i - 1]) for i in range(1, n)))


def median_sorted(values):
    s = sorted(values)
    m = len(s)
    if m == 0:
        return 0.0
    if m % 2:
        return s[m // 2]
    return 0.5 * (s[m // 2 - 1] + s[m // 2])


def detect_clicks(samples, sr, hop=None, max_freq=None, peak=1.0):
    """检测拼接咔哒声。

    判据：
      1) |一阶差分| 超过阈值 theta = max(12 * 中位数, 2*pi*fmax/sr*peak 的 1.5 倍)；
      2) 若有越限点，检查相邻越限间隔是否集中在合成步长 hop（周期性拼接咔哒）。
    返回 dict：越限点数、最大|差分|、理论斜率上界、阈值、越限间隔直方图。
    """
    diffs = first_diffs(samples)
    med = median_sorted(diffs)
    max_d = max(diffs) if diffs else 0.0
    slope_bound = 1.5 * 2.0 * math.pi * (max_freq or 0.0) / sr * peak
    thr = max(12.0 * med, slope_bound)
    idx = [i for i, d in enumerate(diffs) if d > thr]
    intervals = {}
    periodic = False
    if len(idx) > 1 and hop:
        for a, b in zip(idx, idx[1:]):
            key = round((b - a) / hop)
            if key >= 1:
                intervals[key] = intervals.get(key, 0) + 1
        if intervals:
            top = max(intervals, key=intervals.get)
            periodic = intervals[top] >= max(2, len(idx) - 2)
    return {
        'clicks': len(idx),
        'max_diff': max_d,
        'median_diff': med,
        'threshold': thr,
        'slope_bound': slope_bound,
        'indices': idx[:20],
        'periodic_at_hop': periodic,
    }


def content_duration(samples, sr, thresh=1e-3):
    """按 |样本| 超过 thresh 的首尾位置测内容时长（秒）。"""
    first = 0
    last = -1
    for i, v in enumerate(samples):
        if abs(v) > thresh:
            first = i
            break
    for i in range(len(samples) - 1, -1, -1):
        if abs(samples[i]) > thresh:
            last = i
            break
    if last < 0:
        return 0.0
    return (last - first + 1) / sr


def rms(seg):
    if not seg:
        return 0.0
    return math.sqrt(sum(v * v for v in seg) / len(seg))


def cents(measured, expected):
    if measured <= 0 or expected <= 0:
        return float('inf')
    return 1200.0 * math.log2(measured / expected)
