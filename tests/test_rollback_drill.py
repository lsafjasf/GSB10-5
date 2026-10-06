"""回滚演练库自测。运行：python -m unittest discover -s tests -v"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rollback_drill import (  # noqa: E402
    Faults, ManualClock, STEP_SPECS, POLICY,
    build_env, compare_paths, run_pipeline,
)
from rollback_drill.report import benchmark  # noqa: E402

RECORDS = {f"k{i}": f"v{i}" for i in range(30)}


def run_drill(faults=None, timeout_after=None):
    return run_pipeline(build_env("drill", RECORDS, ManualClock(),
                                  faults=faults, timeout_after=timeout_after))


class TestAllSuccess(unittest.TestCase):
    def test_drill_success_all_steps_ok(self):
        rep = run_drill()
        self.assertEqual(rep.outcome, "success")
        self.assertEqual(len(rep.steps), len(STEP_SPECS))
        self.assertTrue(all(s.status == "ok" for s in rep.steps))
        self.assertFalse(rep.tainted)
        # 逐步都有数据与耗时，不允许只有一个总判定
        for s in rep.steps:
            self.assertIsInstance(s.data, dict)
            self.assertGreater(s.duration, 0)
            self.assertGreaterEqual(s.started_at, rep.started_at)
            self.assertLessEqual(s.ended_at, rep.ended_at)

    def test_drill_does_not_touch_production(self):
        from rollback_drill import ManualClock as MC
        clk = MC()
        ctx = build_env("drill", RECORDS, clk)
        before_sum, before_seq = ctx.source.checksum(), ctx.source.write_seq
        rep = run_pipeline(ctx)
        self.assertEqual(rep.outcome, "success")
        # 演练只读线上库：校验和与写序列号不变，恢复落在沙箱副本上
        self.assertEqual(ctx.source.checksum(), before_sum)
        self.assertEqual(ctx.source.write_seq, before_seq)
        self.assertFalse(ctx.source.frozen)
        self.assertNotEqual(ctx.target.name, ctx.source.name)


class TestSingleStepFailure(unittest.TestCase):
    def test_each_step_failure_aborts_and_compensates(self):
        for spec in STEP_SPECS:
            with self.subTest(step=spec.name):
                rep = run_drill(faults=Faults(fail_step=spec.name))
                self.assertEqual(rep.outcome, "aborted")
                self.assertIn(spec.name, rep.abort_reason)
                failed = [s for s in rep.steps if s.status == "failed"]
                self.assertEqual([s.name for s in failed], [spec.name])
                # 失败步骤之后全部 skipped
                idx = [s.name for s in rep.steps].index(spec.name)
                self.assertTrue(all(s.status == "skipped" for s in rep.steps[idx + 1:]))
                # 已完成且有补偿的步骤被逆序补偿
                compensated = {c.name.split(":", 1)[1] for c in rep.compensations}
                done = {s.name for s in rep.steps[:idx] if s.status == "ok"}
                expected = done & {"freeze_writes", "restore_backup"}
                self.assertEqual(compensated, expected)
                # 补偿逆序：restore 的补偿先于 freeze 的补偿
                names = [c.name for c in rep.compensations]
                if "compensate:restore_backup" in names and "compensate:freeze_writes" in names:
                    self.assertLess(names.index("compensate:restore_backup"),
                                    names.index("compensate:freeze_writes"))

    def test_failure_releases_freeze(self):
        rep = run_drill(faults=Faults(fail_step="verify_restore"))
        self.assertEqual(rep.outcome, "aborted")
        self.assertIn("compensate:freeze_writes",
                      [c.name for c in rep.compensations])


class TestNewWritesMidDrill(unittest.TestCase):
    def test_new_writes_mark_and_abort(self):
        rep = run_drill(faults=Faults(write_during_step="restore_backup"))
        self.assertEqual(rep.outcome, "invalidated")
        self.assertTrue(rep.tainted)
        detect = next(s for s in rep.steps if s.name == "detect_new_writes")
        self.assertEqual(detect.status, "ok")
        self.assertEqual(detect.data["new_writes"], 1)
        # 后续步骤被跳过，且执行了补偿
        idx = [s.name for s in rep.steps].index("detect_new_writes")
        self.assertTrue(all(s.status == "skipped" for s in rep.steps[idx + 1:]))
        self.assertTrue(rep.compensations)
        self.assertEqual(POLICY["new_writes"], "mark_and_abort")


class TestTimeout(unittest.TestCase):
    def test_timeout_aborts_with_compensation(self):
        faults = Faults(extra_delay={"restore_backup": 100.0})
        rep = run_drill(faults=faults, timeout_after=2.0)
        self.assertEqual(rep.outcome, "timeout")
        self.assertIn("deadline", rep.abort_reason)
        self.assertTrue(any(s.status == "skipped" for s in rep.steps))
        self.assertTrue(rep.compensations)

    def test_time_is_injected(self):
        # 同一注入时钟下结果完全确定
        r1 = run_drill()
        r2 = run_drill()
        self.assertEqual(r1.to_dict(), r2.to_dict())
        self.assertGreater(r1.duration, 0)  # 耗时来自注入时钟而非真实墙钟


class TestPathConsistency(unittest.TestCase):
    def test_real_and_drill_same_path(self):
        cmp = compare_paths(RECORDS)
        self.assertTrue(cmp["path_identical"])
        self.assertEqual(cmp["real_trace"], cmp["drill_trace"])
        self.assertEqual(cmp["real_trace"], [s.name for s in STEP_SPECS])
        self.assertEqual(cmp["real_outcome"], "success")
        self.assertEqual(cmp["drill_outcome"], "success")
        for row in cmp["steps"]:
            self.assertTrue(row["same_order"])
            self.assertTrue(row["same_code_path"])
        # 差异只在参数：freeze/restore/cleanup 三步有参数差异说明
        diff_steps = {r["step"] for r in cmp["steps"] if r["param_differences"]}
        self.assertEqual(diff_steps, {"freeze_writes", "restore_backup", "cleanup"})


class TestBenchmark(unittest.TestCase):
    def test_benchmark_100(self):
        result = benchmark(100, seed=1)
        self.assertEqual(result["runs"], 100)
        self.assertEqual(len(result["per_run"]), 100)
        total = sum(result["outcome_distribution"].values())
        self.assertEqual(total, 100)
        for key in ("simulated_duration_s", "wall_duration_ms"):
            st = result[key]
            self.assertLessEqual(st["min"], st["median"])
            self.assertLessEqual(st["median"], st["max"])
        self.assertGreater(result["peak_python_memory_kib"], 0)


if __name__ == "__main__":
    unittest.main()
