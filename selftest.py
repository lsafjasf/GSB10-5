"""自测：全自动 / 人工确认 / 审批 / 中断续跑与安全退出 / 接管 / 轨迹核对。"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from runbook import (ACTIONS, Decision, Executor, RunStatus, Runbook, Step,
                     StepKind, action, verify_trace)


@action("noop")
def _noop(params, ctx):
    return {"echo": params}


@action("ctx_put")
def _ctx_put(params, ctx):
    ctx[params["key"]] = params["value"]
    return {"stored": params["key"]}


def make_runbook(kinds, title="t"):
    steps = []
    for i, kind in enumerate(kinds):
        steps.append(Step(f"s{i}", f"step-{i}", kind,
                          "noop" if kind is StepKind.AUTO else "",
                          {"n": i} if kind is StepKind.AUTO else {}))
    return Runbook(title, steps, {"env": "test"})


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="rb-test-"))
        self.trace = self.dir / "trace.jsonl"
        self.ex = Executor(self.dir / "ckpt", self.trace)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def trace_ok(self):
        ok, problems = verify_trace(self.trace)
        self.assertTrue(ok, problems)


class TestFullAuto(Base):
    def test_completes(self):
        st = self.ex.start(make_runbook([StepKind.AUTO] * 5))
        self.assertEqual(st["status"], RunStatus.COMPLETED.value)
        self.assertEqual(st["step_status"], ["succeeded"] * 5)
        self.assertEqual(st["step_index"], 5)
        self.trace_ok()

    def test_io_recorded(self):
        st = self.ex.start(make_runbook([StepKind.AUTO]))
        recs = [json.loads(l) for l in self.trace.read_text().splitlines()]
        started = next(r for r in recs if r["event"] == "step_started")
        finished = next(r for r in recs if r["event"] == "step_finished")
        self.assertEqual(started["payload"]["inputs"], {"n": 0})
        self.assertEqual(finished["payload"]["outputs"], {"echo": {"n": 0}})

    def test_action_failure_marks_failed(self):
        @action("boom")
        def _boom(params, ctx):
            raise ValueError("disk full")
        rb = Runbook("t", [Step("s0", "a", StepKind.AUTO, "noop"),
                           Step("s1", "b", StepKind.AUTO, "boom"),
                           Step("s2", "c", StepKind.AUTO, "noop")])
        st = self.ex.start(rb)
        self.assertEqual(st["status"], RunStatus.FAILED.value)
        self.assertEqual(st["step_status"], ["succeeded", "failed", "pending"])
        self.trace_ok()


class TestConfirm(Base):
    def test_pauses_without_decision_then_resumes(self):
        rb = make_runbook([StepKind.AUTO, StepKind.CONFIRM, StepKind.AUTO])
        st = self.ex.start(rb)  # 无人决策 -> 必须停
        self.assertEqual(st["status"], RunStatus.PAUSED.value)
        self.assertEqual(st["step_index"], 1)
        self.assertEqual(st["step_status"][1], "waiting")
        # 人来了，确认通过 -> 续跑完成
        st2 = self.ex.resume(st["run_id"], lambda s, c: Decision(True, "oncall"))
        self.assertEqual(st2["status"], RunStatus.COMPLETED.value)
        self.assertEqual(st2["step_status"], ["succeeded"] * 3)
        self.assertEqual(st2["decisions"][0]["decided_by"], "oncall")
        self.trace_ok()

    def test_confirm_declined_aborts(self):
        rb = make_runbook([StepKind.CONFIRM, StepKind.AUTO])
        st = self.ex.start(rb, lambda s, c: Decision(False, "oncall", "太危险"))
        self.assertEqual(st["status"], RunStatus.ABORTED.value)
        self.assertEqual(st["step_status"], ["rejected", "pending"])
        self.trace_ok()


class TestApproval(Base):
    def test_approved(self):
        rb = make_runbook([StepKind.AUTO, StepKind.APPROVAL, StepKind.AUTO])
        st = self.ex.start(rb, lambda s, c: Decision(True, "manager", "LGTM"))
        self.assertEqual(st["status"], RunStatus.COMPLETED.value)
        self.trace_ok()

    def test_rejected(self):
        rb = make_runbook([StepKind.AUTO, StepKind.APPROVAL, StepKind.AUTO])
        st = self.ex.start(rb, lambda s, c: Decision(False, "manager", "窗口外"))
        self.assertEqual(st["status"], RunStatus.REJECTED.value)
        self.assertEqual(st["step_status"], ["succeeded", "rejected", "pending"])
        self.trace_ok()


class TestInterrupt(Base):
    def _crashing_runbook(self):
        @action("ctrl_c")
        def _ctrl_c(params, ctx):
            raise KeyboardInterrupt  # 模拟执行到一半被 Ctrl-C
        return Runbook("t", [Step("s0", "a", StepKind.AUTO, "noop"),
                             Step("s1", "b", StepKind.AUTO, "ctrl_c"),
                             Step("s2", "c", StepKind.AUTO, "noop")])

    def test_safe_exit_after_interrupt(self):
        st = self.ex.start(self._crashing_runbook())
        self.assertEqual(st["status"], RunStatus.INTERRUPTED.value)
        self.assertEqual(st["step_index"], 1)  # 停在被断那步
        # 情形一：安全退出 —— 不再跑任何后续步骤
        st2 = self.ex.abort(st["run_id"], "值班人决定改期", operator="oncall")
        self.assertEqual(st2["status"], RunStatus.ABORTED.value)
        self.assertEqual(st2["step_status"], ["succeeded", "running", "pending"])
        self.trace_ok()

    def test_resume_after_interrupt(self):
        st = self.ex.start(self._crashing_runbook())
        self.assertEqual(st["status"], RunStatus.INTERRUPTED.value)
        # 情形二：修好环境后续跑 —— 换掉会炸的动作，从断点继续
        ACTIONS["ctrl_c"] = lambda p, c: {"recovered": True}
        st2 = self.ex.resume(st["run_id"])
        self.assertEqual(st2["status"], RunStatus.COMPLETED.value)
        self.assertEqual(st2["step_status"], ["succeeded"] * 3)
        self.assertEqual(st2["step_index"], 3)
        self.trace_ok()

    def test_hard_crash_keeps_checkpoint(self):
        @action("segfault")
        def _seg(params, ctx):
            raise SystemExit(139)  # 模拟硬崩溃（非 KeyboardInterrupt）
        rb = Runbook("t", [Step("s0", "a", StepKind.AUTO, "noop"),
                           Step("s1", "b", StepKind.AUTO, "segfault"),
                           Step("s2", "c", StepKind.AUTO, "noop")])
        with self.assertRaises(SystemExit):
            self.ex.start(rb)
        # 新执行器（模拟进程重启）从检查点续跑
        ex2 = Executor(self.dir / "ckpt", self.trace)
        ckpt_file = next(ex2.store.dir.glob("*.checkpoint.json"))
        run_id = json.loads(ckpt_file.read_text())["run_id"]
        self.assertTrue(ex2.store.exists(run_id))
        ACTIONS["segfault"] = lambda p, c: {"ok": True}
        st = ex2.resume(run_id)
        self.assertEqual(st["status"], RunStatus.COMPLETED.value)
        self.assertEqual(st["step_status"], ["succeeded"] * 3)
        self.trace_ok()


class TestTakeover(Base):
    def test_takeover_at_pause(self):
        rb = make_runbook([StepKind.AUTO, StepKind.CONFIRM, StepKind.AUTO])
        st = self.ex.start(rb)
        point = self.ex.takeover(st["run_id"], operator="senior-oncall")
        self.assertEqual(point["status_before"], RunStatus.PAUSED.value)
        self.assertEqual(point["status_after"], RunStatus.MANUAL.value)
        self.assertEqual(point["paused_at_step_index"], 1)
        self.assertEqual(point["pending_step"]["id"], "s1")
        self.assertEqual(len(point["remaining_steps"]), 2)
        self.assertEqual(point["context"], {"env": "test"})
        # 接管后执行器拒绝再驱动
        with self.assertRaises(RuntimeError):
            self.ex.resume(st["run_id"])
        ckpt = self.ex.store.load(st["run_id"])
        self.assertEqual(ckpt["status"], RunStatus.MANUAL.value)
        self.trace_ok()


if __name__ == "__main__":
    unittest.main(verbosity=2)
