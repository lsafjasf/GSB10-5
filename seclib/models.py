"""数据模型：信号、资产、事件。全部使用标准库。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import List


class Level(IntEnum):
    """事件级别，P1 最高。数值越小越严重。"""
    P1 = 1
    P2 = 2
    P3 = 3
    P4 = 4

    def higher(self) -> "Level":
        return Level(max(Level.P1, self - 1))

    def lower(self) -> "Level":
        return Level(min(Level.P4, self + 1))


@dataclass(frozen=True)
class Signal:
    """一条检测信号。strength 取值 1~5，由检测器置信度/危害预标定。"""
    name: str
    strength: int  # 1..5

    def __post_init__(self) -> None:
        if not 1 <= self.strength <= 5:
            raise ValueError(f"signal strength out of range: {self.strength}")


@dataclass(frozen=True)
class Asset:
    """被影响资产。criticality 取值 1~5（5=核心生产/涉密）。"""
    asset_id: str
    criticality: int  # 1..5

    def __post_init__(self) -> None:
        if not 1 <= self.criticality <= 5:
            raise ValueError(f"asset criticality out of range: {self.criticality}")


@dataclass
class Event:
    """安全事件。scope_level 取值 1~5，由受影响主机/账号占比映射。"""
    event_id: str
    asset: Asset
    scope_level: int  # 1..5
    signals: List[Signal] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not 1 <= self.scope_level <= 5:
            raise ValueError(f"scope level out of range: {self.scope_level}")
        if not self.signals:
            raise ValueError("event must carry at least one signal")
