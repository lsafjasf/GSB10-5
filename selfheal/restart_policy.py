"""Service self-healing restart policy.

Pure standard library. All time is injected (``clock`` callable or explicit
``now`` arguments), which makes every decision deterministic and testable.

The module implements:

* exponential backoff restart scheduling with a hard cap;
* liveness based on BOTH minimum alive time and health-check results;
* ineffective-restart detection (repeated failed runs -> permanent stop);
* dependency-availability gating (do not restart while a dependency is down;
  probe it slowly and give up after a bounded wait).

Typical usage::

    clock = VirtualClock(0.0)
    mgr = RestartManager(Policy(min_alive_seconds=10.0), clock=clock)
    decision = mgr.on_started(clock.now())
    ...
    decision = mgr.on_exited(clock.now(), exit_code=1)
    if decision.action == Action.RESTART:
        # restart the service at decision.at
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional


class Action(str, Enum):
    """What the supervisor/driver is asked to do."""

    RESTART = "restart"
    """(Re)start the service at ``Decision.at``."""

    PROBE_DEPENDENCY = "probe_dependency"
    """Do not touch the service; re-check the dependency at ``Decision.at``."""

    STOP = "stop"
    """Give up permanently: restarts were ineffective or the dependency
    stayed unavailable past its deadline."""

    NONE = "none"
    """Nothing to do (event acknowledged, keep running)."""


@dataclass(frozen=True)
class Policy:
    # --- liveness -------------------------------------------------------
    min_alive_seconds: float = 10.0
    """A run counts as healthy only if it stays up at least this long AND
    passes health checks. Crashes before this are "quick crashes"."""

    health_check_max_failures: int = 2
    """Consecutive failed health checks that declare a running service dead,
    even though the process itself is still up."""

    # --- backoff --------------------------------------------------------
    base_delay_seconds: float = 1.0
    backoff_factor: float = 2.0
    max_delay_seconds: float = 60.0
    """delay(n) = min(base * factor ** (n - 1), max_delay), n >= 1."""

    max_consecutive_failures: int = 5
    """If this many runs in a row fail, restarts are judged ineffective and
    the manager stops scheduling them."""

    # --- dependency gating ----------------------------------------------
    dependency_probe_interval_seconds: float = 30.0
    """While a dependency is down the service is NOT restarted; the
    dependency itself is re-probed at this slow fixed interval."""

    dependency_max_wait_seconds: float = 600.0
    """If the dependency stays unavailable for this long, give up and stop
    instead of resuming restarts when it comes back."""


@dataclass(frozen=True)
class Decision:
    action: Action = Action.NONE
    at: Optional[float] = None
    reason: str = ""

    def __str__(self) -> str:  # pragma: no cover - debug helper
        when = "now" if self.at is None else f"t={self.at:g}"
        return f"{self.action.value} @ {when} ({self.reason})"


class RestartManager:
    """Event-driven restart supervisor.

    State stored per manager is a fixed set of counters/timestamps only
    (O(1) memory regardless of how many events it receives). The driver is
    expected to perform the returned action at ``Decision.at`` and feed the
    outcome back via the ``on_*`` methods.
    """

    # machine states
    RUNNING = "running"
    WAIT_BACKOFF = "wait_backoff"
    WAIT_DEPENDENCY = "wait_dependency"
    FAILED = "failed"

    def __init__(self, policy: Optional[Policy] = None,
                 clock: Optional[Callable[[], float]] = None) -> None:
        self.policy = policy or Policy()
        self._clock = clock
        self.state: str = "idle"
        self.consecutive_failures: int = 0
        self.total_restarts: int = 0
        self.run_started_at: Optional[float] = None
        self.health_failures: int = 0
        self.dependency_down_since: Optional[float] = None
        self.stop_reason: str = ""
        # Diagnostics only: the algorithm never reads ``history``; it is
        # safe to leave empty to measure the true O(1) state footprint.
        self.history: list[float] = []

    # -- helpers ---------------------------------------------------------

    def now(self) -> float:
        if self._clock is None:
            raise RuntimeError("a clock callable is required")
        return self._clock()

    def backoff_delay(self) -> float:
        attempt = max(1, self.consecutive_failures)
        p = self.policy
        raw = p.base_delay_seconds * p.backoff_factor ** (attempt - 1)
        return min(raw, p.max_delay_seconds)

    def _restart_schedule(self, now: float) -> Decision:
        """Common decision logic after a run failed (crash or unhealthy)."""
        p = self.policy
        self.run_started_at = None
        self.health_failures = 0

        # Ineffective-restart circuit breaker.
        if self.consecutive_failures > p.max_consecutive_failures:
            self.state = self.FAILED
            self.stop_reason = (
                f"restart ineffective: {self.consecutive_failures} consecutive "
                f"failed runs (limit {p.max_consecutive_failures})"
            )
            return Decision(Action.STOP, at=now, reason=self.stop_reason)

        # Dependency gating: never restart into a known-down dependency.
        if self.dependency_down_since is not None:
            waited = now - self.dependency_down_since
            if waited >= p.dependency_max_wait_seconds:
                self.state = self.FAILED
                self.stop_reason = (
                    f"dependency unavailable for {waited:g}s "
                    f"(limit {p.dependency_max_wait_seconds:g}s)"
                )
                return Decision(Action.STOP, at=now, reason=self.stop_reason)
            self.state = self.WAIT_DEPENDENCY
            return Decision(
                Action.PROBE_DEPENDENCY,
                at=now + p.dependency_probe_interval_seconds,
                reason="dependency down; service restart suppressed",
            )

        delay = self.backoff_delay()
        self.state = self.WAIT_BACKOFF
        return Decision(
            Action.RESTART,
            at=now + delay,
            reason=(f"failed run #{self.consecutive_failures}; "
                    f"backoff {delay:g}s"),
        )

    # -- lifecycle events ------------------------------------------------

    def on_started(self, now: Optional[float] = None) -> Decision:
        """Report that the service process was (re)started at ``now``."""
        now = self.now() if now is None else now
        self.run_started_at = now
        self.health_failures = 0
        self.total_restarts += 1
        self.state = self.RUNNING
        return Decision(Action.NONE, at=now, reason="service started")

    def on_exited(self, now: Optional[float] = None,
                  exit_code: int = 1) -> Decision:
        """Report that the service process exited."""
        now = self.now() if now is None else now
        if self.run_started_at is None:
            # Duplicate/stray event; ignore.
            return Decision(Action.NONE, at=now, reason="no active run")

        alive_for = now - self.run_started_at
        healthy_run = (
            alive_for >= self.policy.min_alive_seconds
            and self.health_failures < self.policy.health_check_max_failures
        )
        if healthy_run:
            # A long, healthy run resets the failure streak and the
            # dependency-deadline tracking.
            self.consecutive_failures = 0
        else:
            self.consecutive_failures += 1
        return self._restart_schedule(now)

    def on_health_check(self, ok: bool, now: Optional[float] = None) -> Decision:
        """Report one health-check result while the service is running."""
        now = self.now() if now is None else now
        if self.state != self.RUNNING:
            return Decision(Action.NONE, at=now, reason="not running")
        if ok:
            self.health_failures = 0
            return Decision(Action.NONE, at=now, reason="healthy")

        self.health_failures += 1
        if self.health_failures < self.policy.health_check_max_failures:
            return Decision(
                Action.NONE, at=now,
                reason=f"health check failed {self.health_failures}/"
                       f"{self.policy.health_check_max_failures}",
            )

        # Process is up but the service is dead from the user's point of view:
        # this is a failed run even if the process never exited.
        self.consecutive_failures += 1
        return self._restart_schedule(now)

    def on_dependency_check(self, ok: bool,
                            now: Optional[float] = None) -> Decision:
        """Report whether the long-term dependency (DB, network, ...) is up.

        May be called on the schedule requested by ``PROBE_DEPENDENCY``.
        """
        now = self.now() if now is None else now
        if ok:
            self.dependency_down_since = None
            if self.state == self.WAIT_DEPENDENCY:
                # Dependency recovered: apply normal backoff from the streak.
                delay = self.backoff_delay()
                self.state = self.WAIT_BACKOFF
                return Decision(
                    Action.RESTART, at=now + delay,
                    reason="dependency recovered; schedule restart",
                )
            return Decision(Action.NONE, at=now, reason="dependency up")

        if self.dependency_down_since is None:
            self.dependency_down_since = now

        waited = now - self.dependency_down_since
        if waited >= self.policy.dependency_max_wait_seconds:
            self.state = self.FAILED
            self.stop_reason = (
                f"dependency unavailable for {waited:g}s "
                f"(limit {self.policy.dependency_max_wait_seconds:g}s)"
            )
            return Decision(Action.STOP, at=now, reason=self.stop_reason)

        if self.state == self.RUNNING:
            # Still serving; do nothing yet.
            return Decision(Action.NONE, at=now, reason="dependency down but running")

        self.state = self.WAIT_DEPENDENCY
        return Decision(
            Action.PROBE_DEPENDENCY,
            at=now + self.policy.dependency_probe_interval_seconds,
            reason="dependency down; keep probing only",
        )
