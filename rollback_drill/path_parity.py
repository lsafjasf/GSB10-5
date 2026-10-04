"""Proof that drill and real rollback walk the identical path.

Compares the executed step sequence of a drill run and a real run and emits
per-step parity rows.  Both runs must come from the same runner/step list.
"""

from dataclasses import dataclass
from typing import Any

from .model import RunResult


@dataclass
class ParityRow:
    index: int
    step_id: str
    name: str
    drill_status: str
    real_status: str
    drill_duration_ms: float
    real_duration_ms: float
    same_step: bool
    same_order: bool


def compare_runs(drill: RunResult, real: RunResult) -> list[ParityRow]:
    rows: list[ParityRow] = []
    drill_map = {s.step_id: s for s in drill.steps}
    real_map = {s.step_id: s for s in real.steps}
    all_ids = [s.step_id for s in drill.steps]
    for idx, sid in enumerate(all_ids, start=1):
        d = drill_map.get(sid)
        r = real_map.get(sid)
        rows.append(ParityRow(
            index=idx,
            step_id=sid,
            name=(d or r).name if (d or r) else sid,
            drill_status=d.status.value if d else "MISSING",
            real_status=r.status.value if r else "MISSING",
            drill_duration_ms=round(d.duration_ms, 3) if d else 0.0,
            real_duration_ms=round(r.duration_ms, 3) if r else 0.0,
            same_step=(d is not None and r is not None),
            same_order=(d is not None and r is not None and d.index == r.index),
        ))
    return rows


def parity_table(rows: list[ParityRow]) -> str:
    lines = [
        "| # | step_id | 步骤 | 演练状态 | 真实状态 | 演练耗时ms | 真实耗时ms | 同步骤 | 同顺序 |",
        "|---|---------|------|----------|----------|------------|------------|--------|--------|",
    ]
    for r in rows:
        lines.append(
            f"| {r.index} | {r.step_id} | {r.name} | {r.drill_status} | "
            f"{r.real_status} | {r.drill_duration_ms} | {r.real_duration_ms} | "
            f"{'✓' if r.same_step else '✗'} | {'✓' if r.same_order else '✗'} |"
        )
    return "\n".join(lines)
