"""Run a single rollback drill against a seeded demo environment.

Usage: python3 tools/run_drill.py [--fail-step STEP_ID] [--drift-after STEP_ID]
                                  [--slow-step STEP_ID --slow-seconds N]
Prints the step-by-step result table to stdout.
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rollback_drill import DrillRunner, FaultPlan, FileDataTarget, FileEnv, make_drill_plan
from rollback_drill.report import render_run


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fail-step")
    ap.add_argument("--drift-after")
    ap.add_argument("--slow-step")
    ap.add_argument("--slow-seconds", type=float, default=10.0)
    ap.add_argument("--step-timeout", type=float, default=30.0)
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="drill-cli-"))
    live = {f"db/t-{i:03d}.dat": (f"live-{i}".encode()) * 128 for i in range(30)}
    snap = {f"db/t-{i:03d}.dat": (f"snap-{i}".encode()) * 128 for i in range(30)}
    env = FileEnv.create(tmp / "prod", live, snap)
    fp = FileDataTarget(env.live, env.snap, env.meta).snapshot_fingerprint()
    (env.snap / "manifest.json").write_text(json.dumps({"fingerprint": fp}))

    fault = FaultPlan(
        fail_step=args.fail_step,
        bypass_after=(args.drift_after,) if args.drift_after else (),
        slow_step=args.slow_step, slow_seconds=args.slow_seconds,
    )
    clone = env.clone_to(tmp / "drill")
    res = DrillRunner().run(
        make_drill_plan(clone, run_id_seed="cli", step_timeout_s=args.step_timeout),
        fault)
    print(render_run(res))
    return 0 if res.overall.value == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
