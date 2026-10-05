"""证书吊销状态缓存（仅标准库，时间由调用方注入）。

设计要点（详见 POLICY.md）：
- 过期计算：expires_at = min(fetched_at + ttl, next_update)，now >= expires_at 视为过期。
- 刷新策略：惰性刷新（缓存未命中或过期时才访问上游）。
- 查询失败策略：FAIL_CLOSED / FAIL_OPEN / FAIL_DEGRADED 三选一，见决策表。
- 单调性：一旦判定 REVOKED 即钉住（pin），任何后续刷新都不会把它变回 GOOD。
"""

from __future__ import annotations

import enum
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Protocol, Tuple


class Status(enum.Enum):
    GOOD = "good"
    REVOKED = "revoked"
    UNKNOWN = "unknown"


class QueryTimeout(Exception):
    """上游查询超时。"""


class QueryError(Exception):
    """上游查询其它错误。"""


class Querier(Protocol):
    def query(self, serial: str) -> Tuple[Status, Optional[float]]:
        """查询上游，返回 (状态, next_update)。

        next_update 是上游给出的状态有效期截止时刻（Unix 秒，与 now() 同一时钟），
        可为 None 表示上游未提供。
        """
        ...


class FailMode(enum.Enum):
    FAIL_CLOSED = "fail_closed"    # 查询失败一律拒绝
    FAIL_OPEN = "fail_open"        # 查询失败一律放行（已钉住吊销的除外）
    FAIL_DEGRADED = "fail_degraded"  # 查询失败时在 max_stale 窗口内用旧缓存，否则拒绝


@dataclass(frozen=True)
class Decision:
    serial: str
    allowed: bool
    status: Status
    source: str   # cache | fresh | stale | pinned | fail_closed | fail_open
    reason: str


@dataclass(slots=True)
class _Entry:
    status: Status
    fetched_at: float
    expires_at: float


class RevocationCache:
    def __init__(
        self,
        querier: Querier,
        ttl: float = 300.0,
        fail_mode: FailMode = FailMode.FAIL_CLOSED,
        max_stale: float = 3600.0,
        now: Callable[[], float] = time.time,
    ) -> None:
        if ttl <= 0:
            raise ValueError("ttl must be positive")
        if max_stale < 0:
            raise ValueError("max_stale must be >= 0")
        self._querier = querier
        self._ttl = ttl
        self._fail_mode = fail_mode
        self._max_stale = max_stale
        self._now = now
        self._entries: Dict[str, _Entry] = {}
        self._revoked_pins: Dict[str, float] = {}  # serial -> 钉住时刻
        self._lock = threading.Lock()
        # 统计
        self.hits = 0
        self.misses = 0
        self.fetch_errors = 0
        self.stale_serves = 0

    # ---- 内部 ----

    def _fetch(self, serial: str) -> _Entry:
        status, next_update = self._querier.query(serial)
        fetched_at = self._now()
        expires_at = fetched_at + self._ttl
        if next_update is not None:
            expires_at = min(expires_at, next_update)
        entry = _Entry(status=status, fetched_at=fetched_at, expires_at=expires_at)
        self._entries[serial] = entry
        if status is Status.REVOKED:
            self._revoked_pins.setdefault(serial, fetched_at)
        return entry

    def _on_query_failure(self, serial: str, entry: Optional[_Entry], now: float) -> Decision:
        self.fetch_errors += 1
        if self._fail_mode is FailMode.FAIL_OPEN:
            return Decision(serial, True, Status.UNKNOWN, "fail_open",
                            "query failed; fail-open policy allows")
        if self._fail_mode is FailMode.FAIL_DEGRADED and entry is not None:
            stale_for = now - entry.expires_at
            if 0 <= stale_for <= self._max_stale:
                self.stale_serves += 1
                allowed = entry.status is Status.GOOD
                return Decision(serial, allowed, entry.status, "stale",
                                f"query failed; serving stale entry (stale {stale_for:.1f}s)")
            return Decision(serial, False, Status.UNKNOWN, "fail_closed",
                            "query failed; stale entry beyond max_stale")
        return Decision(serial, False, Status.UNKNOWN, "fail_closed",
                        "query failed; fail-closed policy denies")

    # ---- 对外 ----

    def check(self, serial: str) -> Decision:
        with self._lock:
            now = self._now()

            # 1) 吊销钉住：最高优先级，任何策略下都拒绝，且不再访问上游
            if serial in self._revoked_pins:
                return Decision(serial, False, Status.REVOKED, "pinned",
                                "revoked status is pinned and cannot be refreshed back")

            entry = self._entries.get(serial)

            # 2) 缓存有效：now < expires_at（边界 now == expires_at 视为过期）
            if entry is not None and now < entry.expires_at:
                self.hits += 1
                allowed = entry.status is Status.GOOD
                return Decision(serial, allowed, entry.status, "cache",
                                "served from fresh cache")

            # 3) 未命中或已过期：惰性刷新
            self.misses += 1
            try:
                fresh = self._fetch(serial)
            except (QueryTimeout, QueryError):
                return self._on_query_failure(serial, entry, now)

            allowed = fresh.status is Status.GOOD
            return Decision(serial, allowed, fresh.status, "fresh",
                            "fetched from upstream")

    def is_pinned(self, serial: str) -> bool:
        return serial in self._revoked_pins

    def entry_count(self) -> int:
        return len(self._entries)

    def evict_expired(self) -> int:
        """清理过期且未钉住的条目（可选的内存回收手段）。"""
        with self._lock:
            now = self._now()
            doomed = [s for s, e in self._entries.items()
                      if now >= e.expires_at and s not in self._revoked_pins]
            for s in doomed:
                del self._entries[s]
            return len(doomed)
