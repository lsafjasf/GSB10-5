"""debounce.py — 文件监听事件去抖库（仅标准库，时间可注入）。

设计要点
========
合并粒度：按「目录」合并（bucket key = 事件的父目录）。
  - 一次逻辑写入（编辑器 save、cp、rsync）产生的 create/modify/attrib 等
    一串原始事件都落在同一目录，按目录合并可保证「一次逻辑写入只触发一次」。
  - 不按全局合并：互不相关的两个目录同时写入时不应互相牵连、互相延迟。
  - 不按单路径合并：批量写入 30 个文件会产生 30 次触发，下游仍然被打爆。

去抖窗口（双阈值，二者满足其一即触发）：
  - quiet window（静默窗口 W）：某目录最后一条事件距今 >= W，认为这批写入结束。
  - max wait（最大等待 M）：某目录第一条未决事件距今 >= M，强制触发。
    持续追加写入时静默窗口永远不满足，M 保证触发有界且确定。

乱序与丢失：
  - 合并规则对事件顺序不敏感（按 kind 集合归约，而非状态机），乱序不影响结果。
  - 事件可携带单调递增 seq（按 source 独立计数）。乱序到达的 seq 会被缓存
    等待补齐；触发（flush）时若仍存在未补齐的 seq 空洞，则该次触发带
    resync=True，通知下游对受影响目录做全量重扫，以此补齐丢失事件。
  - 任何「进过 bucket 的路径」必然出现在某次触发中（不漏触发），
    由测试中的全覆盖校验器保证。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

# 事件类型（原始事件）
CREATE = "create"
MODIFY = "modify"
DELETE = "delete"
ATTRIB = "attrib"

# 合并后的逻辑类型
CREATED = "created"
MODIFIED = "modified"
DELETED = "deleted"
CHURN = "churn"  # 窗口内同路径既出现 create 又出现 delete（先建后删/先删后建，乱序安全）


def _merge_kinds(kinds: frozenset) -> str:
    """把一个路径在窗口内出现的所有事件类型归约为一个逻辑类型。

    纯集合运算，与事件到达顺序无关 —— 这是乱序安全的核心。
    """
    if CREATE in kinds and DELETE in kinds:
        return CHURN
    if DELETE in kinds:
        return DELETED
    if CREATE in kinds:
        return CREATED
    return MODIFIED


@dataclass(frozen=True)
class Event:
    """一条原始文件事件。seq 为可选的单调序号（按 source 独立），用于丢包检测。"""

    path: str
    kind: str = MODIFY
    seq: Optional[int] = None
    source: str = "default"


@dataclass(frozen=True)
class Trigger:
    """一次去抖后的逻辑触发：某目录下一批路径的合并结果。"""

    directory: str
    paths: Dict[str, str]  # path -> 合并后的逻辑类型
    resync: bool           # True 表示检测到事件丢失，下游应全量重扫
    first_ts: float
    last_ts: float


@dataclass
class _Bucket:
    first_ts: float
    last_ts: float
    kinds: Dict[str, set] = field(default_factory=dict)  # path -> {kind,...}
    resync: bool = False

    def add(self, path: str, kind: str, ts: float) -> None:
        self.kinds.setdefault(path, set()).add(kind)
        self.last_ts = ts


class Debouncer:
    """事件去抖器。时间通过 clock 注入，测试可用假时钟完全确定性驱动。"""

    def __init__(
        self,
        window: float = 0.5,
        max_wait: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if window <= 0:
            raise ValueError("window must be > 0")
        if max_wait < window:
            raise ValueError("max_wait must be >= window")
        self.window = window
        self.max_wait = max_wait
        self.clock = clock
        self._buckets: Dict[str, _Bucket] = {}
        self._seq_hi: Dict[str, int] = {}        # source -> 已连续确认的最大 seq
        self._seq_pending: Dict[str, set] = {}   # source -> 已收到但前面有空洞的 seq

    # ------------------------------------------------------------------ API

    def feed(self, event: Event) -> None:
        """喂入一条原始事件。只入桶，不触发。"""
        now = self.clock()
        directory = os.path.dirname(event.path) or "."

        self._track_seq(event)
        bucket = self._buckets.get(directory)
        if bucket is None:
            bucket = self._buckets[directory] = _Bucket(first_ts=now, last_ts=now)
        bucket.add(event.path, event.kind, now)

    def tick(self) -> List[Trigger]:
        """推进一次时钟检查，返回所有到期的触发（可能为空）。

        触发条件（满足其一）：
          - 静默：now - bucket.last_ts >= window
          - 封顶：now - bucket.first_ts >= max_wait
        """
        now = self.clock()
        due = [
            d for d, b in self._buckets.items()
            if now - b.last_ts >= self.window or now - b.first_ts >= self.max_wait
        ]
        return [self._flush(d) for d in sorted(due)]

    def drain(self) -> List[Trigger]:
        """无条件清空所有未决桶（例如 watcher 关闭前）。保证不漏触发。"""
        return [self._flush(d) for d in sorted(self._buckets)]

    @property
    def pending_dirs(self) -> int:
        return len(self._buckets)

    @property
    def pending_paths(self) -> int:
        return sum(len(b.kinds) for b in self._buckets.values())

    # ------------------------------------------------------------- internal

    def _track_seq(self, event: Event) -> None:
        """按 source 跟踪 seq。乱序到达的 seq 先缓存，等空洞补齐后再推进。"""
        if event.seq is None:
            return
        src = event.source
        if src not in self._seq_hi:
            self._seq_hi[src] = event.seq  # 首条事件建立基线，不算丢失
            self._seq_pending[src] = set()
            return
        hi = self._seq_hi[src]
        if event.seq <= hi:
            return  # 重复或已确认的迟到事件
        pending = self._seq_pending[src]
        pending.add(event.seq)
        while hi + 1 in pending:  # 空洞被补齐，连续推进
            hi += 1
            pending.discard(hi)
        self._seq_hi[src] = hi

    def _has_seq_gap(self) -> bool:
        """flush 时判定：任一 source 仍有未补齐的 seq 空洞，视为事件丢失。"""
        return any(self._seq_pending.values())

    def _flush(self, directory: str) -> Trigger:
        bucket = self._buckets.pop(directory)
        paths = {
            p: _merge_kinds(frozenset(kinds))
            for p, kinds in sorted(bucket.kinds.items())
        }
        return Trigger(
            directory=directory,
            paths=paths,
            resync=bucket.resync or self._has_seq_gap(),
            first_ts=bucket.first_ts,
            last_ts=bucket.last_ts,
        )
