"""合成变更数据集：带已知真值模型，用于相关性验证。

真实仓库里"是否回滚"的标签需要长期埋点收集；在自测环境中，我们用
一个独立的真值模型生成结局，使验证可重复、可量化：

    logit P(回滚) = b0 + sum_k(beta_k * factor_k) + noise

其中 factor_k 用与评分模型相同的归一化函数计算，但系数 beta 与评分
权重 *故意不同*（且加入噪声与交互项），避免"自己验证自己"的循环论证。
"""
from __future__ import annotations

import math
import random
from typing import Iterator, List, Tuple

from .model import Change, factor_values

# 真值模型系数（与评分权重不同，模拟"评分模型并不完美"的现实）。
TRUE_BETA = {
    "size": 1.6,
    "diffusion": 1.4,
    "criticality": 1.5,
    "test_gap": 1.7,
    "inexperience": 1.0,
    "low_ownership": 0.9,
    "off_hours": 0.5,
}
TRUE_INTERCEPT = -7.2   # 校准到 ~20% 基准回滚率（接近 DORA 中位团队水平）
TRUE_SCALE = 1.7
NOISE_SD = 0.25


def _sample_change(rng: random.Random) -> Change:
    # 潜在"变更难度"：让坏因素共现（大变更往往同时覆盖少、跨模块），
    # 模拟真实仓库中因素间的相关性，避免验证数据过于理想化。
    d = rng.betavariate(2.0, 3.0)
    return Change(
        churn_lines=int(rng.lognormvariate(4.0 + 1.6 * d, 1.1)),
        files_touched=1 + int(rng.expovariate(1 / (2.0 + 6.0 * d))),
        modules_touched=1 + int(rng.expovariate(1 / (1.0 + 2.5 * d))),
        criticality=min(1.0, max(0.0, rng.betavariate(1.5 + 3 * d, 5.0 - 2 * d))),
        test_coverage=min(1.0, max(0.0, rng.betavariate(6.0 - 4 * d, 1.5 + 2 * d))),
        author_commits=1 + int(rng.expovariate(1 / (200.0 - 150.0 * d))),
        ownership_ratio=min(1.0, max(0.0, rng.betavariate(4.0 - 3 * d, 1.5 + 1.5 * d))),
        hour_local=rng.randrange(24),
        is_weekend=rng.random() < 0.12 + 0.1 * d,
    )


def true_failure_probability(change: Change, rng: random.Random | None = None) -> float:
    """真值模型给出的回滚概率（可选加噪声）。"""
    fv = factor_values(change)
    z = TRUE_INTERCEPT + TRUE_SCALE * sum(TRUE_BETA[k] * fv[k] for k in TRUE_BETA)
    # 交互项：大变更 + 低覆盖 额外危险（现实中常见的非线性）
    z += 0.8 * fv["size"] * fv["test_gap"]
    if rng is not None:
        z += rng.gauss(0.0, NOISE_SD)
    return 1.0 / (1.0 + math.exp(-z))


def generate_labeled(
    n: int, seed: int = 2024
) -> Tuple[List[Change], List[int]]:
    """生成 n 条变更及其回滚标签（0/1）。"""
    rng = random.Random(seed)
    changes: List[Change] = []
    outcomes: List[int] = []
    for _ in range(n):
        change = _sample_change(rng)
        p = true_failure_probability(change, rng)
        changes.append(change)
        outcomes.append(1 if rng.random() < p else 0)
    return changes, outcomes


def iter_unlabeled(n: int, seed: int = 99) -> Iterator[Change]:
    """流式生成 n 条变更（供基准测试，不占内存）。"""
    rng = random.Random(seed)
    for _ in range(n):
        yield _sample_change(rng)
