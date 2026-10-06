"""分级算法：信号强度、资产重要性、影响范围三维加权。

设计理由（详见 README.md「分级依据」）：
- 不按告警条数计数，而是先把多条信号聚合成一个“证据强度”，再做加权；
- 聚合取“最强信号 + 其余信号的衰减加成”，使多条互相印证的弱信号可以
  超过单条强信号；
- 资产重要性与影响范围作为乘数环境项，决定同等证据下的处置优先级。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from .models import Event, Level, Signal

# 维度权重：证据 0.50 / 资产 0.30 / 范围 0.20
W_SIGNAL = 0.50
W_ASSET = 0.30
W_SCOPE = 0.20

# 除最强信号外，其余信号按强度的 60% 计入（互相印证的证据加成）
STACK_RATIO = 0.60
# 聚合证据上限，避免堆叠突破标定区间
EVIDENCE_CAP = 6.0

# 级别阈值（含下界），按总分 1.00~5.50 标定
THRESHOLDS: Tuple[Tuple[float, Level], ...] = (
    (4.20, Level.P1),
    (3.20, Level.P2),
    (2.20, Level.P3),
)

# 降级迟滞：当前级别阈值 - HYSTERESIS 才允许降到下一级
HYSTERESIS = 0.30


@dataclass(frozen=True)
class Contribution:
    """单条贡献项：维度、取数、加权分、说明，构成依据清单。"""
    dimension: str
    source: str
    raw: float
    weighted: float
    note: str


@dataclass(frozen=True)
class GradeResult:
    event_id: str
    score: float
    level: Level
    evidence: float
    contributions: Tuple[Contribution, ...]

    def as_table(self) -> List[Tuple[str, str, str, str]]:
        return [
            (c.dimension, c.source, f"{c.raw:.2f}", f"{c.weighted:.3f}")
            for c in self.contributions
        ]


def aggregate_evidence(signals: List[Signal]) -> Tuple[float, List[Signal]]:
    """最强信号得满分，其余信号按 STACK_RATIO 加成。

    4 条强度 2 的弱信号 = 2 + 3*2*0.6 = 5.6 > 单条强信号 5.0，
    从而体现“几条弱信号叠加可能比一条强信号更严重”。
    """
    ordered = sorted(signals, key=lambda s: s.strength, reverse=True)
    strongest = ordered[0]
    stacked = STACK_RATIO * sum(s.strength for s in ordered[1:])
    return min(EVIDENCE_CAP, strongest.strength + stacked), ordered


def classify(score: float) -> Level:
    for threshold, level in THRESHOLDS:
        if score >= threshold:
            return level
    return Level.P4


def grade(event: Event) -> GradeResult:
    evidence, ordered = aggregate_evidence(event.signals)
    contributions: List[Contribution] = []

    strongest = ordered[0]
    contributions.append(
        Contribution(
            "证据强度", strongest.name, float(strongest.strength),
            strongest.strength * W_SIGNAL, "最强信号取全值",
        )
    )
    for extra in ordered[1:]:
        weighted = extra.strength * STACK_RATIO * W_SIGNAL
        contributions.append(
            Contribution(
                "证据强度", extra.name, float(extra.strength), weighted,
                f"佐证信号按 {STACK_RATIO:.0%} 折算",
            )
        )

    contributions.append(
        Contribution(
            "资产重要性", event.asset.asset_id, float(event.asset.criticality),
            event.asset.criticality * W_ASSET, "资产关键度 1~5",
        )
    )
    contributions.append(
        Contribution(
            "影响范围", f"{event.event_id}#scope", float(event.scope_level),
            event.scope_level * W_SCOPE, "受影响范围 1~5",
        )
    )

    score = sum(c.weighted for c in contributions)
    return GradeResult(
        event_id=event.event_id,
        score=round(score, 4),
        level=classify(score),
        evidence=round(evidence, 4),
        contributions=tuple(contributions),
    )


def lower_threshold(level: Level) -> float:
    """当前级别的进入阈值；低于 阈值-HYSTERESIS 才允许降级。"""
    for threshold, lv in THRESHOLDS:
        if lv == level:
            return threshold
    return 0.0
