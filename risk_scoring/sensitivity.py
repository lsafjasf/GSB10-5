"""权重敏感度分析：回答"权重变了，结论还稳不稳"。

三种互补的扰动方式：
1. 全局抖动：所有权重乘 U(1-delta, 1+delta) 的随机因子，重复 trials 次，
   观察排序相关性（Spearman rho）与等级判定翻转率。
2. 单因素扫描：每次只把一个因素的权重在 ±delta 内变化，观察该因素
   对排序/判定的最大影响，定位"敏感权重"。
3. Dirichlet 重采样：模拟"权重估计本身有噪声"的场景，给出结论
   （等级判定）保持不变的概率。

稳健性判据（用于测试与报告）：
- 全局抖动 delta=0.2 时 Spearman rho >= 0.9
- 等级判定一致率 >= 0.75
"""
from __future__ import annotations

import random
from typing import Dict, List, Mapping, Sequence

from .model import (
    DEFAULT_WEIGHTS,
    FACTOR_NAMES,
    Change,
    factor_values,
    normalized_weights,
    score_change,
    score_from_values,
)


def _ranks(values: Sequence[float]) -> List[float]:
    """平均秩（处理并列）。"""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman_rho(a: Sequence[float], b: Sequence[float]) -> float:
    """Spearman 秩相关系数。"""
    if len(a) != len(b) or len(a) < 2:
        raise ValueError("两个序列长度需一致且 >= 2")
    ra, rb = _ranks(a), _ranks(b)
    n = len(a)
    mean_a = sum(ra) / n
    mean_b = sum(rb) / n
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(ra, rb))
    var_a = sum((x - mean_a) ** 2 for x in ra)
    var_b = sum((y - mean_b) ** 2 for y in rb)
    if var_a == 0 or var_b == 0:
        return 1.0
    return cov / (var_a * var_b) ** 0.5


def _jitter_weights(
    base: Mapping[str, float], delta: float, rng: random.Random
) -> Dict[str, float]:
    return {k: v * rng.uniform(1.0 - delta, 1.0 + delta) for k, v in base.items()}


def _dirichlet_weights(
    base: Mapping[str, float], concentration: float, rng: random.Random
) -> Dict[str, float]:
    """以 base*concentration 为参数的 Dirichlet 采样（用 Gamma 变量实现）。"""
    gammas = {k: rng.gammavariate(max(v * concentration, 1e-9), 1.0)
              for k, v in base.items()}
    total = sum(gammas.values())
    return {k: g / total for k, g in gammas.items()}


def _precompute(changes, base):
    """每个变更只算一次归一化因素值，并预计算基准分数/等级。"""
    values = [factor_values(c) for c in changes]
    base_w = normalized_weights(base)
    scores = [score_from_values(v, base_w)[0] for v in values]
    bands = [score_from_values(v, base_w)[1] for v in values]
    return values, base_w, scores, bands


def global_jitter(
    changes: Sequence[Change],
    base_weights: Mapping[str, float] | None = None,
    delta: float = 0.2,
    trials: int = 200,
    seed: int = 42,
) -> Dict[str, float]:
    """全局抖动：返回排序相关性与判定一致率。"""
    base = dict(DEFAULT_WEIGHTS if base_weights is None else base_weights)
    rng = random.Random(seed)
    values, _, base_scores, base_bands = _precompute(changes, base)

    rhos: List[float] = []
    agreements: List[float] = []
    n = len(changes)
    for _ in range(trials):
        w = _jitter_weights(base, delta, rng)
        scored = [score_from_values(v, w) for v in values]
        scores = [s for s, _ in scored]
        bands = [b for _, b in scored]
        rhos.append(spearman_rho(base_scores, scores))
        agreements.append(sum(1 for x, y in zip(base_bands, bands) if x == y) / n)

    rhos.sort()
    agreements.sort()
    return {
        "delta": delta,
        "trials": trials,
        "spearman_min": rhos[0],
        "spearman_mean": sum(rhos) / len(rhos),
        "agreement_min": agreements[0],
        "agreement_mean": sum(agreements) / len(agreements),
    }


def one_factor_sweep(
    changes: Sequence[Change],
    base_weights: Mapping[str, float] | None = None,
    delta: float = 0.2,
    steps: int = 9,
) -> List[Dict[str, float]]:
    """单因素扫描：每个因素单独在 ±delta 内变化，报告其最大影响。"""
    base = dict(DEFAULT_WEIGHTS if base_weights is None else base_weights)
    values, _, base_scores, base_bands = _precompute(changes, base)
    n = len(changes)

    report_rows: List[Dict[str, float]] = []
    for factor in FACTOR_NAMES:
        worst_rho = 1.0
        worst_agreement = 1.0
        for step in range(steps):
            scale = 1.0 - delta + 2.0 * delta * step / (steps - 1)
            w = dict(base)
            w[factor] = base[factor] * scale
            scored = [score_from_values(v, w) for v in values]
            scores = [s for s, _ in scored]
            bands = [b for _, b in scored]
            worst_rho = min(worst_rho, spearman_rho(base_scores, scores))
            worst_agreement = min(
                worst_agreement,
                sum(1 for x, y in zip(base_bands, bands) if x == y) / n,
            )
        report_rows.append(
            {
                "factor": factor,
                "base_weight": base[factor],
                "worst_spearman": worst_rho,
                "worst_agreement": worst_agreement,
            }
        )
    report_rows.sort(key=lambda row: row["worst_spearman"])
    return report_rows


def dirichlet_stability(
    changes: Sequence[Change],
    base_weights: Mapping[str, float] | None = None,
    concentration: float = 200.0,
    trials: int = 200,
    seed: int = 7,
) -> Dict[str, float]:
    """Dirichlet 重采样：等级判定保持不变的概率。

    concentration 越小扰动越大（200 ≈ 每权重标准差 ~7%）。
    """
    base = dict(DEFAULT_WEIGHTS if base_weights is None else base_weights)
    rng = random.Random(seed)
    values, _, _, base_bands = _precompute(changes, base)
    n = len(changes)
    keep = 0
    for _ in range(trials):
        w = _dirichlet_weights(base, concentration, rng)
        bands = [score_from_values(v, w)[1] for v in values]
        keep += sum(1 for x, y in zip(base_bands, bands) if x == y)
    return {
        "concentration": concentration,
        "trials": trials,
        "band_keep_rate": keep / (trials * n),
    }
