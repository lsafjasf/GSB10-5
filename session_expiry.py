from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass
from typing import Callable


ACTIVE = "active"
IDLE_EXPIRED = "idle_expired"
ABSOLUTE_EXPIRED = "absolute_expired"
NOT_FOUND = "not_found"


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    session_id: str
    valid: bool
    reason: str
    idle_remaining: float
    absolute_remaining: float
    idle_expires_at: float | None
    absolute_expires_at: float | None


class ManualClock:
    __slots__ = ("now",)

    def __init__(self, start: float = 0.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def set(self, value: float) -> float:
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("clock time must be finite")
        self.now = value
        return self.now

    def advance(self, seconds: float) -> float:
        seconds = float(seconds)
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("clock advance must be finite and non-negative")
        self.now += seconds
        return self.now


class _Session:
    __slots__ = ("created_at", "last_activity_at", "absolute_expires_at")

    def __init__(self, now: float, absolute_timeout: float) -> None:
        self.created_at = now
        self.last_activity_at = now
        self.absolute_expires_at = now + absolute_timeout


class SessionManager:
    def __init__(
        self,
        idle_timeout: float,
        absolute_timeout: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._idle_timeout = _positive_timeout("idle_timeout", idle_timeout)
        self._absolute_timeout = _positive_timeout("absolute_timeout", absolute_timeout)
        self._clock = clock
        self._last_now: float | None = None
        self._sessions: dict[str, _Session] = {}

    def create(self, session_id: str | None = None) -> SessionSnapshot:
        now = self._effective_now()
        if session_id is None:
            session_id = uuid.uuid4().hex
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id must be a non-empty string")
        if session_id in self._sessions:
            raise ValueError(f"session already exists: {session_id}")

        session = _Session(now, self._absolute_timeout)
        self._sessions[session_id] = session
        return self._snapshot(session_id, session, now)

    def get(self, session_id: str) -> SessionSnapshot:
        now = self._effective_now()
        session = self._sessions.get(session_id)
        if session is None:
            return _missing_snapshot(session_id)
        return self._snapshot(session_id, session, now)

    def touch(self, session_id: str) -> SessionSnapshot:
        now = self._effective_now()
        session = self._sessions.get(session_id)
        if session is None:
            return _missing_snapshot(session_id)

        snapshot = self._snapshot(session_id, session, now)
        if not snapshot.valid:
            return snapshot

        session.last_activity_at = now
        return self._snapshot(session_id, session, now)

    def purge_expired(self) -> int:
        now = self._effective_now()
        expired = [
            session_id
            for session_id, session in self._sessions.items()
            if not self._snapshot(session_id, session, now).valid
        ]
        for session_id in expired:
            del self._sessions[session_id]
        return len(expired)

    def _effective_now(self) -> float:
        raw_now = float(self._clock())
        if not math.isfinite(raw_now):
            raise ValueError("clock returned a non-finite time")
        if self._last_now is None or raw_now > self._last_now:
            self._last_now = raw_now
        return self._last_now

    def _snapshot(
        self, session_id: str, session: _Session, now: float
    ) -> SessionSnapshot:
        idle_expires_at = session.last_activity_at + self._idle_timeout
        absolute_expires_at = session.absolute_expires_at
        idle_remaining = idle_expires_at - now
        absolute_remaining = absolute_expires_at - now

        if absolute_remaining <= 0:
            valid = False
            reason = ABSOLUTE_EXPIRED
        elif idle_remaining <= 0:
            valid = False
            reason = IDLE_EXPIRED
        else:
            valid = True
            reason = ACTIVE

        return SessionSnapshot(
            session_id=session_id,
            valid=valid,
            reason=reason,
            idle_remaining=max(0.0, idle_remaining),
            absolute_remaining=max(0.0, absolute_remaining),
            idle_expires_at=idle_expires_at,
            absolute_expires_at=absolute_expires_at,
        )


def _missing_snapshot(session_id: str) -> SessionSnapshot:
    return SessionSnapshot(
        session_id=session_id,
        valid=False,
        reason=NOT_FOUND,
        idle_remaining=0.0,
        absolute_remaining=0.0,
        idle_expires_at=None,
        absolute_expires_at=None,
    )


def _positive_timeout(name: str, value: float) -> float:
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return value
