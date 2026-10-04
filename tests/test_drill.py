"""Self-tests for the rollback-drill library.

Covers: all-success, single-step failure at every step, mid-run new writes
(drift), drill timeout, path parity between drill and real rollback,
scheduling, and a 100-iteration timing run.
"""

import json
import statistics
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rollback_drill import (  # noqa: E402
    DrillRunner, DrillScheduler, FaultPlan, FileDataTarget, FileEnv,
    JsonlAuditSink, Mode, Status, make_drill_plan, make_real_plan,
)
from rollback_drill.clock import FakeClock  # noqa: E402
from rollback_drill.path_parity import compare_runs  # noqa: E402
from rollback_drill.target import build_steps, tree_digest  # noqa: E402

STEP_IDS = [s.step_id for s in build_steps()]


def seed_env(root: Path, n_files: int = 20, payload: int = 500) -> FileEnv:
    live = {f"db/table-{i:03d}.dat": bytes([i % 251]) * payload for i in range(n_files)}
    snap = {f"db/table-{i:03d}.dat": b"SNAP" + bytes([i % 251]) * (payload - 4)
            for i in range(n_files)}
    env = FileEnv.create(root, live, snap)
    fp = FileDataTarget(env.live, env.snap, env.meta).snapshot_fingerprint()
    (env.snap / "manifest.json").write_text(json.dumps({"fingerprint": fp}))
    return env


class DrillTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="drill-test-"))
        self.env = seed_env(self.tmp / "prod")
        self.prod_fp_before = tree_digest(self.env.live)
        self.snap_fp = FileDataTarget(self.env.live, self.env.snap,
                                      self.env.meta).snapshot_fingerprint()

    def fresh_drill(self, name="drill", clock=None, **plan_kw):
        clone = self.env.clone_to(self.tmp / name)
        runner = DrillRunner(clock=clock or FakeClock())
        plan = make_drill_plan(clone, run_id_seed=name, **plan_kw)
        return runner, plan, clone


class TestAllSuccess(DrillTestBase):
    def test_drill_success_step_by_step(self):
        runner, plan, clone = self.fresh_drill()
        res = runner.run(plan)
        self.assertEqual(res.overall, Status.SUCCESS)
        self.assertEqual([s.step_id for s in res.steps], STEP_IDS)
        for s in res.steps:
            self.assertEqual(s.status, Status.SUCCESS, s.step_id)
            self.assertTrue(s.checks, f"{s.step_id} must carry judgement data")
            for c in s.checks:
                self.assertTrue(c.passed, f"{s.step_id}:{c.name}")
                self.assertTrue(c.expected and c.actual)
        # drill restored the clone, production untouched
        self.assertEqual(tree_digest(clone.live),
                         FileDataTarget(clone.live, clone.snap, clone.meta).snapshot_fingerprint())
        self.assertEqual(tree_digest(self.env.live), self.prod_fp_before)

    def test_real_run_restores_production(self):
        runner = DrillRunner(clock=FakeClock())
        res = runner.run(make_real_plan(self.env))
        self.assertEqual(res.overall, Status.SUCCESS)
        self.assertEqual(tree_digest(self.env.live), self.snap_fp)

    def test_drill_and_real_walk_identical_path(self):
        d_runner, d_plan, _ = self.fresh_drill("d1")
        drill = d_runner.run(d_plan)
        real = DrillRunner(clock=FakeClock()).run(make_real_plan(self.env))
        self.assertEqual([s.step_id for s in drill.steps],
                         [s.step_id for s in real.steps])
        rows = compare_runs(drill, real)
        self.assertEqual(len(rows), len(STEP_IDS))
        for row in rows:
            self.assertTrue(row.same_step, row.step_id)
            self.assertTrue(row.same_order, row.step_id)
            self.assertEqual(row.drill_status, row.real_status)

    def test_plan_signature_stable_and_mode_sensitive(self):
        _, plan_a, _ = self.fresh_drill("sig-a")
        _, plan_b, _ = self.fresh_drill("sig-b")
        self.assertEqual(plan_a.signature(), plan_a.signature())
        self.assertNotEqual(plan_a.signature(), plan_b.signature())  # plan_id differs
        real_plan = make_real_plan(self.env)
        drill_same_id = make_drill_plan(self.env.clone_to(self.tmp / "sig-c"),
                                        run_id_seed="prod")
        self.assertNotEqual(real_plan.signature(), drill_same_id.signature())


class TestSingleStepFailure(DrillTestBase):
    def test_every_step_failure_is_deterministic(self):
        for sid in STEP_IDS:
            with self.subTest(step=sid):
                runner, plan, clone = self.fresh_drill(f"fail-{sid}")
                res = runner.run(plan, FaultPlan(fail_step=sid))
                self.assertEqual(res.overall, Status.FAILED)
                idx = STEP_IDS.index(sid)
                for before in res.steps[:idx]:
                    self.assertEqual(before.status, Status.SUCCESS, before.step_id)
                failed = res.steps[idx]
                self.assertEqual(failed.status, Status.FAILED)
                self.assertTrue(failed.error)
                for after in res.steps[idx + 1:]:
                    self.assertEqual(after.status, Status.SKIPPED, after.step_id)
                self.assertEqual(len(res.steps), len(STEP_IDS))
                self._assert_policy_outcome(sid, failed, clone, res)

    def _assert_policy_outcome(self, sid, failed, clone, res):
        target = FileDataTarget(clone.live, clone.snap, clone.meta)
        if sid in ("s01_precheck", "s02_verify_snapshot"):
            self.assertFalse(failed.compensated)          # nothing to undo
            self.assertFalse(target.is_fenced())          # fence never engaged
        elif sid in ("s03_fence_writes", "s04_capture_safety"):
            self.assertTrue(failed.compensated)           # ABORT -> unfence
            self.assertFalse(target.is_fenced())
        elif sid in ("s05_restore", "s06_verify_restored", "s07_drift_check"):
            self.assertTrue(failed.compensated)           # ROLLBACK -> safety copy
            self.assertEqual(tree_digest(clone.live), self.prod_fp_before)
            self.assertTrue(target.is_fenced())           # stays safely closed
        else:  # s08/s09/s10 -> MARK
            self.assertFalse(failed.compensated)
            self.assertTrue(res.marked_for_manual)
            self.assertTrue(res.mark_reason.startswith("MANUAL_"))


class TestMidRunWrites(DrillTestBase):
    def test_drift_detected_and_rolled_back(self):
        audit = self.tmp / "audit-drift.jsonl"
        runner, plan, clone = self.fresh_drill("drift")
        runner.audit = JsonlAuditSink(str(audit))
        res = runner.run(plan, FaultPlan(bypass_after=("s05_restore",)))
        self.assertEqual(res.overall, Status.FAILED)
        s07 = res.find("s07_drift_check")
        self.assertEqual(s07.status, Status.FAILED)
        self.assertTrue(s07.compensated)
        drift_check = s07.checks[0]
        self.assertFalse(drift_check.passed)
        self.assertNotEqual(drift_check.expected, drift_check.actual)
        # deterministic outcome: safety copy restored, fence left engaged
        self.assertEqual(tree_digest(clone.live), self.prod_fp_before)
        self.assertTrue(FileDataTarget(clone.live, clone.snap, clone.meta).is_fenced())
        for s in res.steps[7:]:
            self.assertEqual(s.status, Status.SKIPPED)
        events = [json.loads(l)["event"] for l in audit.read_text().splitlines()]
        self.assertIn("BYPASS_WRITE_DETECTED", events)
        self.assertIn("COMPENSATED", events)


class TestTimeout(DrillTestBase):
    def test_step_timeout_aborts_and_rolls_back(self):
        runner, plan, clone = self.fresh_drill("slow", step_timeout_s=5.0)
        res = runner.run(plan, FaultPlan(slow_step="s05_restore", slow_seconds=10.0))
        self.assertTrue(res.timeout)
        self.assertEqual(res.overall, Status.FAILED)
        s05 = res.find("s05_restore")
        self.assertEqual(s05.status, Status.FAILED)
        self.assertIn("timeout", s05.error)
        self.assertTrue(s05.compensated)
        self.assertEqual(tree_digest(clone.live), self.prod_fp_before)
        for s in res.steps[5:]:
            self.assertEqual(s.status, Status.SKIPPED)

    def test_total_timeout(self):
        runner, plan, clone = self.fresh_drill(
            "slow-total", step_timeout_s=100.0, total_timeout_s=5.0)
        res = runner.run(plan, FaultPlan(slow_step="s03_fence_writes",
                                         slow_seconds=10.0))
        self.assertTrue(res.timeout)
        self.assertEqual(res.overall, Status.FAILED)


class TestScheduler(unittest.TestCase):
    def test_periodic_schedule(self):
        clock = FakeClock()
        sched = DrillScheduler(interval_s=3600.0, clock=clock)
        runs = []
        self.assertTrue(sched.due())
        sched.maybe_run(lambda: runs.append(1) or "run-1")
        self.assertFalse(sched.due())
        clock.advance(3599.0)
        self.assertFalse(sched.due())
        clock.advance(2.0)
        self.assertTrue(sched.due())
        sched.maybe_run(lambda: runs.append(1) or "run-2")
        self.assertEqual(len(runs), 2)
        self.assertEqual(len(sched.history), 2)


class TestHundredRuns(DrillTestBase):
    def test_100_drills_all_success_with_timing(self):
        durations = []
        for i in range(100):
            clone = self.env.clone_to(self.tmp / f"b{i}")
            runner = DrillRunner()  # real clock: measures true cost
            res = runner.run(make_drill_plan(clone, run_id_seed=f"b{i}"))
            self.assertEqual(res.overall, Status.SUCCESS, f"run {i}")
            durations.append(res.total_ms)
        durations.sort()
        p50 = statistics.median(durations)
        p95 = durations[int(len(durations) * 0.95) - 1]
        self.assertLess(p50, 1000.0)
        self.assertLess(p95, 3000.0)
        print(f"\n100 drills: p50={p50:.2f}ms p95={p95:.2f}ms "
              f"max={durations[-1]:.2f}ms")


if __name__ == "__main__":
    unittest.main(verbosity=2)
