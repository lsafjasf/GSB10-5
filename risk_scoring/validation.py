"""评分与实际后果（是否回滚）的相关性验证。

指标（全部用标准库实现）：
- 点二列相关（point-biserial）：分数与 0/1 结局的 Pearson 相关。
- AUC：随机取一对(回滚, 未回滚)，分数排序正确的概率（Mann-Whitney U）。
- 阈值判定：precision / recall / F1 / 混淆矩阵。
- 分档失败率：验证"分数越高、实际回滚率越高"的单调性。
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from .model import THRESHOLD_HIGH, THRESHOLD_MEDIUM


def point_biserial(scores: Sequence[float], outcomes: Sequence[int]) -> float:
    """点二列相关系数（连续分数 vs 0/1 结局）。"""
    n = len(scores)
    if n != len(outcomes) or n < 2:
        raise ValueError("scores 与 outcomes 长度需一致且 >= 2")
    n1 = sum(outcomes)
    n0 = n - n1
    if n1 == 0 or n0 == 0:
        raise ValueError("结局必须同时包含 0 和 1")
    mean1 = sum(s for s, o in zip(scores, outcomes) if o) / n1
    mean0 = sum(s for s, o in zip(scores, outcomes) if not o) / n0
    mean_all = sum(scores) / n
    var = sum((s - mean_all) ** 2 for s in scores) / n
    if var == 0:
        raise ValueError("分数无方差")
    sd = var ** 0.5
    return (mean1 - mean0) / sd * ((n1 * n0) / (n * n)) ** 0.5


def auc(scores: Sequence[float], outcomes: Sequence[int]) -> float:
    """ROC AUC（秩和法，正确处理并列）。"""
    pairs = sorted(zip(scores, outcomes), key=lambda p: p[0])
    # 平均秩
    ranks = [0.0] * len(pairs)
    i = 0
    while i < len(pairs):
        j = i
        while j + 1 < len(pairs) and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[k] = avg
        i = j + 1
    n1 = sum(outcomes)
    n0 = len(outcomes) - n1
    if n1 == 0 or n0 == 0:
        raise ValueError("结局必须同时包含 0 和 1")
    rank_sum_pos = sum(r for r, (_, o) in zip(ranks, pairs) if o == 1)
    return (rank_sum_pos - n1 * (n1 + 1) / 2.0) / (n1 * n0)


def confusion_at_threshold(
    scores: Sequence[float], outcomes: Sequence[int], threshold: float
) -> Dict[str, float]:
    """以 threshold 为界预测"会回滚"，返回混淆矩阵与 precision/recall/F1。"""
    tp = fp = tn = fn = 0
    for s, o in zip(scores, outcomes):
        pred = s >= threshold
        if pred and o:
            tp += 1
        elif pred and not o:
            fp += 1
        elif not pred and o:
            fn += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "threshold": threshold,
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "precision": precision, "recall": recall, "f1": f1,
    }


def band_failure_rates(
    scores: Sequence[float], outcomes: Sequence[int]
) -> List[Dict[str, float]]:
    """按低/中/高分档统计实际回滚率，检验单调性。"""
    bands = [
        ("low", 0.0, THRESHOLD_MEDIUM),
        ("medium", THRESHOLD_MEDIUM, THRESHOLD_HIGH),
        ("high", THRESHOLD_HIGH, 101.0),
    ]
    rows = []
    for name, lo, hi in bands:
        idx = [i for i, s in enumerate(scores) if lo <= s < hi]
        n = len(idx)
        fails = sum(outcomes[i] for i in idx)
        rows.append(
            {
                "band": name,
                "n": n,
                "failures": fails,
                "failure_rate": fails / n if n else 0.0,
            }
        )
    return rows


def validate(scores: Sequence[float], outcomes: Sequence[int]) -> Dict[str, object]:
    """汇总全部验证指标。"""
    return {
        "n": len(scores),
        "base_failure_rate": sum(outcomes) / len(outcomes),
        "point_biserial": point_biserial(scores, outcomes),
        "auc": auc(scores, outcomes),
        "confusion_at_medium": confusion_at_threshold(scores, outcomes, THRESHOLD_MEDIUM),
        "confusion_at_high": confusion_at_threshold(scores, outcomes, THRESHOLD_HIGH),
        "band_failure_rates": band_failure_rates(scores, outcomes),
    }
