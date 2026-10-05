"""文件监听事件去抖器（仅标准库，时间由调用方注入）。

核心模型：源端产生原始事件 (path, kind, ts, seq)，调用方在拿到事件或
时钟推进时调用 tick(now)，tick 返回本轮到期、合并后的逻辑事件。

合并粒度：
  1. 按路径合并：同一 path 在静默窗口 window 内的连续事件合并为一次。
  2. 目录整批折叠：同一目录下同一轮到期的不同 path 数量 >= dir_threshold
     时，折叠为一个目录级事件（目录整批替换 / 批量写入同一目录）。
  3. 最大等待 max_wait：持续写入时，自本轮首个事件起满 max_wait 强制
     触发，新事件自动开启下一轮，保证触发时刻确定。

乱序 / 丢失处理见 feed()、tick()、reconcile() 中的说明。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Dict, FrozenSet, Iterable, List, Optional, Set, Tuple

# 触发原因
REASON_QUIET = "quiet"            # 静默窗口到期
REASON_MAX_WAIT = "max_wait"      # 达到最大等待（持续写入兜底）
REASON_SEQ_GAP = "seq_gap"        # 序列号断档，疑似丢事件，立即补齐触发
REASON_RECONCILE = "reconcile"    # 快照对账补齐
REASON_DIR_COLLAPSE = "dir_collapse"  # 目录整批折叠


@dataclass
class FiredEvent:
    """去抖后对外触发的一次逻辑事件。"""

    key: str
    is_dir_event: bool
    kinds: FrozenSet[str]
    count: int          # 被合并的原始事件数
    first_ts: float
    last_ts: float
    reason: str


@dataclass
class _KeyState:
    first_ts: float
    last_ts: float
    kinds: Set[str] = field(default_factory=set)
    count: int = 0
    last_seq: Optional[int] = None
    flush_reason: Optional[str] = None  # 置位后下一次 tick 立即触发


class Debouncer:
    def __init__(
        self,
        window: float = 0.05,
        max_wait: float = 0.5,
        dir_threshold: int = 8,
        clock: Optional[Callable[[], float]] = None,
    ):
        if window <= 0 or max_wait < window:
            raise ValueError("要求 0 < window <= max_wait")
        self.window = window
        self.max_wait = max_wait
        self.dir_threshold = dir_threshold
        self._clock = clock
        self._pending: Dict[str, _KeyState] = {}
        self._sealed: List[Tuple[str, _KeyState]] = []  # feed 中封板、待 tick 结算
        self._min_deadline: Optional[float] = None  # 未决状态最早可能到期时刻
        self._snapshot: Optional[Set[str]] = None

        # 统计（供校验）
        self.events_in = 0
        self.events_out = 0
        self.dir_collapses = 0
        self.gaps_detected = 0

    def _now(self) -> float:
        if self._clock is None:
            raise RuntimeError("未注入 clock：请给事件/tick 显式传时间戳")
        return self._clock()

    def feed(
        self,
        path: str,
        kind: str = "modify",
        ts: Optional[float] = None,
        seq: Optional[int] = None,
    ) -> None:
        """喂入一个原始文件事件。时间戳 ts 与序列号 seq 均可缺省。

        乱序处理：事件的 ts 可以早于该 path 已收到的 ts。乱序事件绝不丢弃，
        仍计入 count/kinds；时间窗口按已见时间的极值计算（first 取最小、
        last 取最大），因此乱序只会让窗口更保守（稍晚触发），不会漏触发。

        持续写入：新事件到达时若距本轮首个事件已 >= max_wait，则当前轮
        立即标记到期，新事件开启下一轮——触发时刻因此是确定的
        （first_ts + k * max_wait），与 tick 调用时机无关。

        丢失处理：源端若提供单调递增 seq，发现 seq > last_seq + 1 的断档时
        标记立即触发，下一次 tick 不等静默窗口直接补齐。
        """
        now = self._now() if ts is None else ts
        self.events_in += 1

        st = self._pending.get(path)
        if st is not None and st.flush_reason is None and now - st.first_ts >= self.max_wait:
            # 本轮已写满 max_wait：封板，新事件开启下一轮
            st.flush_reason = REASON_MAX_WAIT
            self._sealed.append((path, st))
            del self._pending[path]
            st = None

        if st is None:
            st = _KeyState(first_ts=now, last_ts=now)
            self._pending[path] = st
        else:
            if now < st.first_ts:
                st.first_ts = now
            if now > st.last_ts:
                st.last_ts = now

        st.kinds.add(kind)
        st.count += 1

        if seq is not None:
            if st.last_seq is not None and seq > st.last_seq + 1:
                # 序列号断档：中间至少丢了 1 个事件，立即补齐
                self.gaps_detected += 1
                if st.flush_reason is None:
                    st.flush_reason = REASON_SEQ_GAP
            if st.last_seq is None or seq > st.last_seq:
                st.last_seq = seq

        if st.flush_reason is not None:
            deadline = float("-inf")
        else:
            q = st.last_ts + self.window
            m = st.first_ts + self.max_wait
            deadline = q if q < m else m
        if self._min_deadline is None or deadline < self._min_deadline:
            self._min_deadline = deadline

    def _deadline(self, st: _KeyState) -> float:
        """该状态最早可能到期的时刻；被标记立即触发时为 -inf。"""
        if st.flush_reason is not None:
            return float("-inf")
        return min(st.last_ts + self.window, st.first_ts + self.max_wait)

    def tick(self, now: Optional[float] = None) -> List[FiredEvent]:
        """推进到时刻 now，返回本轮触发的全部逻辑事件。

        到期条件（满足任一）：已被标记立即触发（断档/写满 max_wait/对账）、
        静默 >= window、自首个事件起等待 >= max_wait。

        到期后按目录分组：同目录到期 path 数 >= dir_threshold 时折叠为
        单个目录级事件，否则按各自 path 触发。
        """
        now = self._now() if now is None else now

        # 快速路径：无封板且堆顶截止时间未到期，无需扫描未决表
        if not self._sealed and (
            self._min_deadline is None or now < self._min_deadline
        ):
            return []

        due: List[Tuple[str, _KeyState]] = list(self._sealed)
        self._sealed.clear()
        for key, st in list(self._pending.items()):
            if (
                st.flush_reason is not None
                or now - st.last_ts >= self.window
                or now - st.first_ts >= self.max_wait
            ):
                due.append((key, st))
                del self._pending[key]

        # 同一路径可能同时有“封板轮”和“新一轮”两条到期记录，按条处理
        by_dir: Dict[str, List[Tuple[str, _KeyState]]] = {}
        for key, st in due:
            by_dir.setdefault(os.path.dirname(key), []).append((key, st))

        fired: List[FiredEvent] = []
        for directory, entries in by_dir.items():
            if len(entries) >= self.dir_threshold:
                kinds: Set[str] = set()
                count = 0
                first_ts = float("inf")
                last_ts = float("-inf")
                for _, st in entries:
                    kinds |= st.kinds
                    count += st.count
                    first_ts = min(first_ts, st.first_ts)
                    last_ts = max(last_ts, st.last_ts)
                fired.append(
                    FiredEvent(
                        key=directory,
                        is_dir_event=True,
                        kinds=frozenset(kinds),
                        count=count,
                        first_ts=first_ts,
                        last_ts=last_ts,
                        reason=REASON_DIR_COLLAPSE,
                    )
                )
                self.dir_collapses += 1
            else:
                for key, st in entries:
                    if st.flush_reason is not None:
                        reason = st.flush_reason
                    elif now - st.first_ts >= self.max_wait:
                        reason = REASON_MAX_WAIT
                    else:
                        reason = REASON_QUIET
                    fired.append(
                        FiredEvent(
                            key=key,
                            is_dir_event=False,
                            kinds=frozenset(st.kinds),
                            count=st.count,
                            first_ts=st.first_ts,
                            last_ts=st.last_ts,
                            reason=reason,
                        )
                    )

        self.events_out += len(fired)
        # 重新计算最早截止时刻（只在发生扫描的 tick 上付出 O(N)）
        self._min_deadline = None
        for st in self._pending.values():
            deadline = self._deadline(st)
            if self._min_deadline is None or deadline < self._min_deadline:
                self._min_deadline = deadline
        return fired

    def reconcile(
        self, snapshot: Iterable[str], now: Optional[float] = None
    ) -> List[FiredEvent]:
        """快照对账（无序列号时的丢事件补齐机制）。

        调用方周期性提供当前真实存在的 path 集合；与上次快照做差集：
        新出现的 path 合成 create，消失的 path 合成 delete。合成事件标记
        立即触发（不等静默窗口），保证即使底层事件整体丢失，真实变更在
        下一次对账时确定地补齐触发；无变化时不产生任何触发。
        """
        now = self._now() if now is None else now
        current = set(snapshot)

        if self._snapshot is not None:
            added = current - self._snapshot
            removed = self._snapshot - current
            for path in sorted(added):
                self.feed(path, "create", now, seq=None)
                self._pending[path].flush_reason = REASON_RECONCILE
                self._min_deadline = float("-inf")
            for path in sorted(removed):
                self.feed(path, "delete", now, seq=None)
                self._pending[path].flush_reason = REASON_RECONCILE
                self._min_deadline = float("-inf")

        self._snapshot = current
        return self.tick(now)

    def pending_count(self) -> int:
        return len(self._pending)
