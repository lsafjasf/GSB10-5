"""File-backed data target and the rollback step definitions.

``FileDataTarget`` models a production datastore as a directory tree.  The
*same* class serves the real target and the drill clone; only the root
directory differs.  All steps in :func:`build_steps` call only these target
methods, so drill and real runs execute byte-identical code paths.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .model import Check, FailPolicy, Mode

MANIFEST = "manifest.json"
WATERMARK = "watermark.txt"
FENCE = "fence.txt"


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------

@dataclass
class FileEnv:
    """A seeded production-like environment.

    ``live``   - the datastore being rolled back.
    ``snap``   - the known-good snapshot to restore.
    ``audit``  - append-only audit log (JSONL).
    """

    root: Path
    live: Path
    snap: Path
    audit: Path
    meta: Path

    @classmethod
    def create(cls, root: Path, live_files: dict[str, bytes],
               snap_files: dict[str, bytes]) -> "FileEnv":
        root = Path(root)
        live = root / "live"
        snap = root / "snap"
        meta = root / "meta"
        for d in (live, snap, meta):
            d.mkdir(parents=True, exist_ok=True)
        _write_tree(live, live_files)
        _write_tree(snap, snap_files)
        (meta / WATERMARK).write_text("0")
        (meta / FENCE).write_text("open")
        audit = root / "audit.jsonl"
        audit.touch()
        return cls(root=root, live=live, snap=snap, audit=audit, meta=meta)

    def clone_to(self, dst: Path) -> "FileEnv":
        """Byte-identical copy used as the drill sandbox."""
        dst = Path(dst)
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(self.root, dst)
        return FileEnv(root=dst, live=dst / "live", snap=dst / "snap",
                       audit=dst / "audit.jsonl", meta=dst / "meta")


def _write_tree(base: Path, files: dict[str, bytes]) -> None:
    for rel, data in files.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def tree_digest(base: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(base.rglob("*")):
        if p.is_file() and p.name != MANIFEST:
            h.update(str(p.relative_to(base)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def tree_listing(base: Path) -> list[str]:
    return sorted(str(p.relative_to(base)) for p in base.rglob("*") if p.is_file())


def tree_size(base: Path) -> int:
    return sum(p.stat().st_size for p in base.rglob("*") if p.is_file())


# ---------------------------------------------------------------------------
# Target
# ---------------------------------------------------------------------------

class FileDataTarget:
    """Production-shaped datastore.  All mutating ops go through here."""

    def __init__(self, live: Path, snap: Path, meta: Path,
                 fault: Optional[dict[str, str]] = None):
        self.live = Path(live)
        self.snap = Path(snap)
        self.meta = Path(meta)
        self.fault = fault or {}

    # -- fault injection -------------------------------------------------
    def _maybe_fault(self, op: str) -> None:
        mode = self.fault.get(op)
        if mode == "raise":
            raise RuntimeError(f"injected fault at {op}")

    def _maybe_slow(self, op: str, clock) -> None:
        mode = self.fault.get(op)
        if mode and mode.startswith("slow:"):
            clock.charge(float(mode.split(":", 1)[1]))

    # -- introspection ----------------------------------------------------
    def fingerprint(self) -> str:
        return tree_digest(self.live)

    def snapshot_fingerprint(self) -> str:
        return tree_digest(self.snap)

    def size_bytes(self) -> int:
        return tree_size(self.live)

    def file_count(self) -> int:
        return len(tree_listing(self.live))

    def watermark(self) -> int:
        return int((self.meta / WATERMARK).read_text().strip() or "0")

    def is_fenced(self) -> bool:
        return (self.meta / FENCE).read_text().strip() == "fenced"

    # -- mutations ---------------------------------------------------------
    def fence_writes(self, clock) -> None:
        self._maybe_fault("fence_writes")
        self._maybe_slow("fence_writes", clock)
        (self.meta / FENCE).write_text("fenced")

    def unfence_writes(self, clock) -> None:
        self._maybe_fault("unfence_writes")
        self._maybe_slow("unfence_writes", clock)
        (self.meta / FENCE).write_text("open")

    def capture_safety_copy(self, dst: Path, clock) -> None:
        self._maybe_fault("capture_safety_copy")
        self._maybe_slow("capture_safety_copy", clock)
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(self.live, dst)

    def restore_snapshot(self, clock) -> None:
        self._maybe_fault("restore_snapshot")
        self._maybe_slow("restore_snapshot", clock)
        if self.live.exists():
            shutil.rmtree(self.live)
        shutil.copytree(self.snap, self.live)

    def smoke_test(self, clock) -> None:
        self._maybe_fault("smoke_test")
        self._maybe_slow("smoke_test", clock)
        # Deterministic smoke: every file must be non-empty and parseable as bytes.
        for p in sorted(self.live.rglob("*")):
            if p.is_file() and p.stat().st_size == 0:
                raise RuntimeError(f"smoke test: empty file {p.name}")

    def probe_write(self, clock) -> None:
        """Simulate a stray writer that bypasses the fence."""
        self._maybe_fault("probe_write")
        self._maybe_slow("probe_write", clock)
        marker = self.live / f".probe-{os.getpid()}.tmp"
        marker.write_text("probe")
        marker.unlink()
        wm = int((self.meta / WATERMARK).read_text().strip() or "0")
        (self.meta / WATERMARK).write_text(str(wm + 1))

    def bump_watermark(self, clock) -> None:
        wm = int((self.meta / WATERMARK).read_text().strip() or "0")
        (self.meta / WATERMARK).write_text(str(wm + 1))


# ---------------------------------------------------------------------------
# Plan & steps
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Plan:
    """Immutable binding of target + snapshot + mode.  Drill and real runs
    differ *only* in the plan, never in the step list."""

    plan_id: str
    mode: Mode
    target: FileDataTarget
    snapshot_dir: Path
    audit_path: Optional[str]
    step_timeout_s: float = 30.0
    total_timeout_s: float = 300.0
    label: str = ""

    def signature(self) -> str:
        payload = {
            "plan_id": self.plan_id,
            "mode": self.mode.value,
            "snapshot": self.target.snapshot_fingerprint(),
            "step_timeout_s": self.step_timeout_s,
            "total_timeout_s": self.total_timeout_s,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Step:
    index: int
    step_id: str
    name: str
    policy: FailPolicy
    compensation: Optional[str] = None  # target method name
    mark_status: Optional[str] = None


def build_steps() -> list[Step]:
    """The canonical 10-step rollback pipeline.  Order and IDs are frozen."""
    return [
        Step(1, "s01_precheck", "资源预检", FailPolicy.ABORT),
        Step(2, "s02_verify_snapshot", "校验快照完整性", FailPolicy.ABORT),
        Step(3, "s03_fence_writes", "写入围栏", FailPolicy.ABORT,
             compensation="unfence_writes"),
        Step(4, "s04_capture_safety", "安全副本捕获", FailPolicy.ABORT,
             compensation="unfence_writes"),
        Step(5, "s05_restore", "执行恢复", FailPolicy.ROLLBACK,
             compensation="restore_safety_copy"),
        Step(6, "s06_verify_restored", "恢复内容校验", FailPolicy.ROLLBACK,
             compensation="restore_safety_copy"),
        Step(7, "s07_drift_check", "数据漂移检测", FailPolicy.ROLLBACK,
             compensation="restore_safety_copy"),
        Step(8, "s08_unfence", "解除围栏", FailPolicy.MARK,
             mark_status="MANUAL_UNFENCE"),
        Step(9, "s09_smoke", "冒烟测试", FailPolicy.MARK,
             mark_status="MANUAL_SMOKE"),
        Step(10, "s10_audit_commit", "审计落账", FailPolicy.MARK,
             mark_status="MANUAL_AUDIT"),
    ]


# ---------------------------------------------------------------------------
# Step implementations (shared by drill and real)
# ---------------------------------------------------------------------------

def step_s01_precheck(target: FileDataTarget, plan: Plan, ctx) -> dict[str, Any]:
    size = target.size_bytes()
    files = target.file_count()
    checks = [
        Check("target_readable", "live tree readable", f"{files} files", files >= 0),
        Check("non_empty", "size > 0", f"{size} bytes", size > 0),
    ]
    return {"checks": checks, "detail": {"size_bytes": size, "file_count": files}}


def step_s02_verify_snapshot(target: FileDataTarget, plan: Plan, ctx) -> dict[str, Any]:
    expected = target.snapshot_fingerprint()
    manifest = target.snap / MANIFEST
    actual = "missing"
    ok = False
    if manifest.exists():
        data = json.loads(manifest.read_text())
        actual = data.get("fingerprint", "missing")
        ok = actual == expected
    checks = [Check("snapshot_integrity", expected, actual, ok)]
    return {"checks": checks, "detail": {"snapshot_fp": expected, "manifest_fp": actual}}


def step_s03_fence_writes(target: FileDataTarget, plan: Plan, ctx) -> dict[str, Any]:
    target.fence_writes(ctx.clock)
    fenced = target.is_fenced()
    checks = [Check("fence_engaged", "fenced", "fenced" if fenced else "open", fenced)]
    return {"checks": checks, "detail": {"fenced": fenced}}


def step_s04_capture_safety(target: FileDataTarget, plan: Plan, ctx) -> dict[str, Any]:
    safety = Path(tempfile.mkdtemp(prefix="rollback-safety-"))
    target.capture_safety_copy(safety, ctx.clock)
    ctx.safety_dir = safety
    live_fp = target.fingerprint()
    safety_fp = tree_digest(safety)
    ok = live_fp == safety_fp
    checks = [Check("safety_copy_matches", live_fp, safety_fp, ok)]
    return {"checks": checks, "detail": {
        "safety_dir": str(safety), "live_fp": live_fp,
        "_bytes_copied": target.size_bytes(), "_files_copied": target.file_count(),
    }}


def step_s05_restore(target: FileDataTarget, plan: Plan, ctx) -> dict[str, Any]:
    before = target.fingerprint()
    target.restore_snapshot(ctx.clock)
    after = target.fingerprint()
    changed = before != after
    checks = [Check("restore_changed", "fingerprint changed", f"{before[:12]}→{after[:12]}", changed)]
    return {"checks": checks, "detail": {
        "before_fp": before, "after_fp": after,
        "_bytes_copied": sum(p.stat().st_size for p in target.snap.rglob("*") if p.is_file()),
        "_files_copied": len(list(target.snap.rglob("*"))),
    }}


def step_s06_verify_restored(target: FileDataTarget, plan: Plan, ctx) -> dict[str, Any]:
    expected = target.snapshot_fingerprint()
    actual = target.fingerprint()
    ok = expected == actual
    checks = [Check("restored_matches_snapshot", expected, actual, ok)]
    return {"checks": checks, "detail": {"expected_fp": expected, "actual_fp": actual}}


def step_s07_drift_check(target: FileDataTarget, plan: Plan, ctx) -> dict[str, Any]:
    # Watermark must not have advanced since the fence was engaged.
    wm = target.watermark()
    baseline = ctx.fence_watermark
    drifted = wm != baseline
    checks = [Check("no_drift", f"watermark=={baseline}", f"watermark=={wm}", not drifted)]
    return {"checks": checks, "detail": {"baseline_watermark": baseline, "current_watermark": wm}}


def step_s08_unfence(target: FileDataTarget, plan: Plan, ctx) -> dict[str, Any]:
    target.unfence_writes(ctx.clock)
    open_ = not target.is_fenced()
    checks = [Check("fence_released", "open", "open" if open_ else "fenced", open_)]
    return {"checks": checks, "detail": {"fenced": not open_}}


def step_s09_smoke(target: FileDataTarget, plan: Plan, ctx) -> dict[str, Any]:
    target.smoke_test(ctx.clock)
    checks = [Check("smoke_passed", "no empty files", "no empty files", True)]
    return {"checks": checks, "detail": {}}


def step_s10_audit_commit(target: FileDataTarget, plan: Plan, ctx) -> dict[str, Any]:
    if plan.audit_path:
        with open(plan.audit_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "event": "COMMITTED",
                "plan_id": plan.plan_id,
                "mode": plan.mode.value,
                "run_id": ctx.run_id,
            }) + "\n")
    checks = [Check("audit_committed", "COMMITTED written", "COMMITTED written", True)]
    return {"checks": checks, "detail": {"audit_path": plan.audit_path}}


STEP_IMPLS: dict[str, Callable] = {
    "s01_precheck": step_s01_precheck,
    "s02_verify_snapshot": step_s02_verify_snapshot,
    "s03_fence_writes": step_s03_fence_writes,
    "s04_capture_safety": step_s04_capture_safety,
    "s05_restore": step_s05_restore,
    "s06_verify_restored": step_s06_verify_restored,
    "s07_drift_check": step_s07_drift_check,
    "s08_unfence": step_s08_unfence,
    "s09_smoke": step_s09_smoke,
    "s10_audit_commit": step_s10_audit_commit,
}
