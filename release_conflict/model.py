"""数据模型：资源、发布单、冲突条目、排期结果。"""

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Dict, Mapping, Tuple


@dataclass(frozen=True)
class Resource:
    """发布要占用的资源。

    capacity 表示同一时刻允许的最大并行占用量：
    - capacity = 1 表示独占资源（同一时刻只允许一个发布占用）；
    - capacity = k 表示共享资源（如"数据库最多并行 2 个变更"）。
    """

    id: str
    capacity: int = 1

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("resource id 不能为空")
        if self.capacity < 1:
            raise ValueError(f"资源 {self.id} 的 capacity 必须 >= 1")


@dataclass(frozen=True)
class Release:
    """发布单：在 [start, end) 半开区间内占用一组资源。

    resource_demands: {资源 id: 占用量}，占用量必须为正整数。
    """

    id: str
    start: int
    end: int
    resource_demands: Mapping[str, int]

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("release id 不能为空")
        if self.end <= self.start:
            raise ValueError(f"发布单 {self.id} 的窗口非法：end 必须大于 start")
        demands: Dict[str, int] = {}
        for rid, amount in dict(self.resource_demands).items():
            if amount <= 0:
                raise ValueError(f"发布单 {self.id} 对资源 {rid} 的占用量必须为正整数")
            demands[rid] = int(amount)
        if not demands:
            raise ValueError(f"发布单 {self.id} 至少要占用一个资源")
        object.__setattr__(self, "resource_demands", MappingProxyType(demands))

    @property
    def duration(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class Conflict:
    """一条冲突：某个资源上的一组发布单形成的最小冲突集。

    - resource: 发生争抢的共享资源 id；
    - releases: 参与冲突的发布单 id（升序，>= 2 个）；
    - overlap: 这些发布单共同的占用时段 [lo, hi)；
    - demand / capacity: 该资源在 overlap 内被同时占用的总量与容量。
    """

    resource: str
    releases: Tuple[str, ...]
    overlap: Tuple[int, int]
    demand: int
    capacity: int

    @property
    def size(self) -> int:
        return len(self.releases)


@dataclass(frozen=True)
class ScheduledRelease:
    """排期结果：为发布单安排的实际占用窗口 [start, end)。"""

    release: Release
    start: int
    end: int
