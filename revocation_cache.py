"""证书吊销状态缓存（仅标准库）。

时间与查询源均通过构造函数注入，便于测试。
状态机与策略详见 POLICY.md。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, Optional


class Status(Enum):
    GOOD = "good"
    REVOKED = "revoked"
    UNKNOWN = "unknown"


class UnavailablePolicy(Enum):
    """查询不可用（超时 / 报错 / 上游 UNKNOWN）时的策略。"""

    DENY = "deny"        # 安全优先：一律拒绝（默认）
    ALLOW = "allow"      # 可用性优先：一律放行（结论标记 no-data，不担保有效）
    DEGRADED = "degraded"  # 有限降级：stale 窗口内使用旧值，窗口外拒绝


class RevocationSourceError(Exception):
    """吊销源报错（连接失败、5xx、解析失败等）。"""


class RevocationTimeoutError(RevocationSourceError):
    """吊销源超时。"""


RevocationFetcher = Callable[[str], Status]
Clock = Callable[[], float]

ALLOW = "ALLOW"
REJECT = "REJECT"


@dataclass(frozen=True)
class Verdict:
    decision: str          # ALLOW / REJECT
    status: Status         # 据以判定的状态（REVOKED/GOOD/UNKNOWN）
    source: str            # live / cache / stale / no-data
    reason: str

    @property
    def allowed(self) -> bool:
        return self.decision == ALLOW


class _Entry:
    __slots__ = ("status", "fetched_at", "expires_at", "revoked_sticky")

    def __init__(self, status: Status, fetched_at: float, expires_at: float,
                 revoked_sticky: bool = False) -> None:
        self.status = status
        self.fetched_at = fetched_at
        self.expires_at = expires_at
        self.revoked_sticky = revoked_sticky


class RevocationCache:
    """吊销状态缓存。

    - ttl: 条目有效期（秒）。fresh 当且仅当 now < expires_at；
      now == expires_at 即视为过期（边界确定、可测）。
    - stale_window: DEGRADED 策略下，过期后仍可使用旧值的最长时间（秒）。
      可用当且仅当 now < expires_at + stale_window。
    - policy: 查询不可用时的策略，默认 DENY。
    - clock / fetcher 注入；fetcher 抛 RevocationTimeoutError/
      RevocationSourceError 或返回 UNKNOWN 均视为“查询不可用”。
    """

    def __init__(
        self,
        fetcher: RevocationFetcher,
        ttl: float = 900.0,
        stale_window: float = 300.0,
        policy: UnavailablePolicy = UnavailablePolicy.DENY,
        clock: Clock = time.monotonic,
    ) -> None:
        if ttl <= 0:
            raise ValueError("ttl must be > 0")
        if stale_window < 0:
            raise ValueError("stale_window must be >= 0")
        self._fetcher = fetcher
        self._ttl = float(ttl)
        self._stale_window = float(stale_window)
        self._policy = policy
        self._clock = clock
        self._entries: Dict[str, _Entry] = {}

    def check(self, serial: str) -> Verdict:
        now = self._clock()
        entry = self._entries.get(serial)
        if entry is None:
            return self._fetch_and_store(serial, now)
        if now < entry.expires_at:
            return self._from_entry(entry, source="cache", now=now)
        return self._refresh(serial, entry, now)

    def invalidate(self, serial: str) -> None:
        """主动失效（推送侧收到 CRL/OCSP 通知时调用）。"""
        self._entries.pop(serial, None)

    def _fetch_and_store(self, serial: str, now: float) -> Verdict:
        try:
            status = self._fetcher(serial)
        except (RevocationTimeoutError, RevocationSourceError):
            return self._unavailable(None)
        if status is Status.UNKNOWN:
            return self._unavailable(None)
        entry = _Entry(status, now, now + self._ttl,
                       status is Status.REVOKED)
        self._entries[serial] = entry
        return self._from_entry(entry, source="live", now=now)

    def _refresh(self, serial: str, entry: _Entry, now: float) -> Verdict:
        # 粘性吊销条目：查询失败也继续拒绝；查询成功也不允许翻案。
        try:
            status = self._fetcher(serial)
        except (RevocationTimeoutError, RevocationSourceError):
            if entry.revoked_sticky:
                return Verdict(REJECT, Status.REVOKED, "stale",
                               "revoked sticky; refresh failed")
            return self._unavailable(entry)
        if status is Status.UNKNOWN:
            if entry.revoked_sticky:
                return Verdict(REJECT, Status.REVOKED, "stale",
                               "revoked sticky; upstream unknown")
            return self._unavailable(entry)
        if entry.revoked_sticky or status is Status.REVOKED:
            new_entry = _Entry(Status.REVOKED, now, now + self._ttl, True)
            self._entries[serial] = new_entry
            return Verdict(REJECT, Status.REVOKED, "live",
                           "revoked sticky" if entry.revoked_sticky
                           else "revoked")
        new_entry = _Entry(status, now, now + self._ttl, False)
        self._entries[serial] = new_entry
        return self._from_entry(new_entry, source="live", now=now)

    def _unavailable(self, stale: Optional[_Entry]) -> Verdict:
        if self._policy is UnavailablePolicy.ALLOW:
            return Verdict(ALLOW, Status.UNKNOWN, "no-data",
                           "source unavailable; fail-open")
        if self._policy is UnavailablePolicy.DEGRADED and stale is not None:
            now = self._clock()
            if now < stale.expires_at + self._stale_window:
                return self._from_entry(stale, source="stale", now=now,
                                        note="served stale during outage")
        if stale is not None and stale.revoked_sticky:
            return Verdict(REJECT, Status.REVOKED, "stale",
                           "revoked sticky; source unavailable")
        return Verdict(REJECT, Status.UNKNOWN, "no-data",
                       "source unavailable; fail-closed")

    @staticmethod
    def _from_entry(entry: _Entry, source: str, now: float,
                    note: Optional[str] = None) -> Verdict:
        if entry.status is Status.REVOKED:
            reason = note or ("revoked sticky" if entry.revoked_sticky
                              else "revoked")
            return Verdict(REJECT, Status.REVOKED, source, reason)
        reason = note or "good"
        return Verdict(ALLOW, Status.GOOD, source, reason)
