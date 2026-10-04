"""Generate sample drill reports for the four required scenarios plus the
drill-vs-real path parity table.

Usage: python3 tools/gen_samples.py
Outputs: reports/drill_{success,step_failure,drift,timeout}.md,
         docs/PATH_PARITY.md
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rollback_drill import (
    DrillRunner, FaultPlan, FileDataTarget, FileEnv, make_drill_plan, make_real_plan,
)
from rollback_drill.clock import FakeClock
from rollback_drill.path_parity import compare_runs, parity_table
from rollback_drill.report import render_run


def seed_env(root: Path) -> FileEnv:
    live = {f"db/table-{i:03d}.dat": (f"live-v2-{i}".encode()) * 64 for i in range(40)}
    snap = {f"db/table-{i:03d}.dat": (f"snap-v1-{i}".encode()) * 64 for i in range(40)}
    env = FileEnv.create(root, live, snap)
    fp = FileDataTarget(env.live, env.snap, env.meta).snapshot_fingerprint()
    (env.snap / "manifest.json").write_text(json.dumps({"fingerprint": fp}))
    return env


def main() -> int:
    base = Path(__file__).resolve().parent.parent
    reports = base / "reports"
    reports.mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="drill-samples-"))

    # 1. all success
    env = seed_env(tmp / "prod")
    clone = env.clone_to(tmp / "d-success")
    res = DrillRunner().run(make_drill_plan(clone, run_id_seed="sample-success"))
    (reports / "drill_success.md").write_text(render_run(res))

    # 2. single-step failure (s06 verify restored)
    clone = env.clone_to(tmp / "d-stepfail")
    res = DrillRunner().run(make_drill_plan(clone, run_id_seed="sample-stepfail"),
                            FaultPlan(fail_step="s06_verify_restored"))
    (reports / "drill_step_failure.md").write_text(render_run(res))

    # 3. mid-run new writes (drift)
    clone = env.clone_to(tmp / "d-drift")
    res = DrillRunner().run(make_drill_plan(clone, run_id_seed="sample-drift"),
                            FaultPlan(bypass_after=("s05_restore",)))
    (reports / "drill_drift.md").write_text(render_run(res))

    # 4. timeout (injected clock keeps it deterministic and instant)
    clone = env.clone_to(tmp / "d-timeout")
    res = DrillRunner(clock=FakeClock()).run(
        make_drill_plan(clone, run_id_seed="sample-timeout", step_timeout_s=5.0),
        FaultPlan(slow_step="s05_restore", slow_seconds=10.0))
    (reports / "drill_timeout.md").write_text(render_run(res))

    # 5. path parity: drill vs real on the same seeded environment
    env2 = seed_env(tmp / "prod2")
    drill = DrillRunner().run(
        make_drill_plan(env2.clone_to(tmp / "d-parity"), run_id_seed="parity"))
    real = DrillRunner().run(make_real_plan(env2, run_id_seed="parity"))
    rows = compare_runs(drill, real)
    doc = "\n".join([
        "# 演练与真实回滚路径对照",
        "",
        "演练与真实回滚由同一个 `DrillRunner.run` 执行同一个冻结步骤表",
        "（`rollback_drill.target.build_steps`），唯一差异是 `Plan` 绑定：",
        "演练指向生产环境的逐字节克隆（`FileEnv.clone_to`），真实回滚指向生产目录本身。",
        "下表为同一份种子数据上两次真实执行的对照数据（由 `tools/gen_samples.py` 生成）。",
        "",
        parity_table(rows),
        "",
        f"- 演练计划签名: `{drill.plan_signature}`，真实计划签名: `{real.plan_signature}`",
        "  （签名含模式与目标绑定，二者必须不同；步骤序列必须相同）",
        f"- 步骤序列一致: `{[s.step_id for s in drill.steps] == [s.step_id for s in real.steps]}`",
        "",
    ])
    (base / "docs" / "PATH_PARITY.md").write_text(doc)
    print("wrote reports/drill_*.md and docs/PATH_PARITY.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
