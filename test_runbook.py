"""自测：python3 -m unittest -v  或  python3 test_runbook.py
覆盖：全自动 / 人工确认 / 审批 / 拒绝 / 中断续跑 / 安全退出 / 接管 / 轨迹完整性。
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from runbook import (
    AutoOperator, Decision, Executor, Runbook, ScriptedOperator, Step,
    StepKind, TraceRecorder,
)
from runbook.actions import noop
from runbook.trace import read_trace
from runbooks import approval_book, confirm_book, full_auto_book, interrupt_book


class RunbookTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="runbook-test-")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def executor(self, book, op, name="t", **kw):
        trace = TraceRecorder(os.path.join(self.tmp, f"{name}.jsonl"),
                              name, book.book_id)
        self.addCleanup(trace.close)
        return Executor(book, op, trace, checkpoint_dir=self.tmp, **kw), trace


class FullAutoTests(RunbookTestBase):
    def test_runs_all_steps_without_human(self):
        ex, trace = self.executor(full_auto_book(), AutoOperator())
        state = ex.run()
        self.assertIs(state.value, "completed")
        self.assertEqual([r.status for r in ex.records.values()],
                         ["ok", "ok", "ok", "ok"])

    def test_auto_operator_rejects_human_step(self):
        bad = Runbook("rb-bad", "含人工步骤",
                      [Step("x", "确认", StepKind.CONFIRM, prompt="?")])
        ex, _ = self.executor(bad, AutoOperator())
        with self.assertRaises(RuntimeError):
            ex.run()


class HumanGateTests(RunbookTestBase):
    def test_stops_when_human_absent_and_resumes(self):
        book = confirm_book()
        ex, trace = self.executor(book, ScriptedOperator([None]), name="h1")
        state = ex.run()
        # 关键不变式：人不在，必须停在确认点，绝不自动往下冲
        self.assertIs(state, type(state).WAITING_HUMAN)
        self.assertEqual(ex.next_index, 1)  # s1 完成，s2 未执行
        self.assertEqual(ex.records["s1"].status, "ok")
        self.assertNotIn("s2", ex.records)

        # 从检查点续跑，两次确认均批准
        trace2 = TraceRecorder(os.path.join(self.tmp, "h1.jsonl"),
                               ex.run_id, book.book_id)
        self.addCleanup(trace2.close)
        ex2 = Executor.resume(
            os.path.join(self.tmp, f"{ex.run_id}.checkpoint.json"),
            book, ScriptedOperator([
                Decision("approve", "zhangsan"),
                Decision("approve", "zhangsan"),
            ]), trace2)
        state2 = ex2.run()
        self.assertIs(state2, type(state2).COMPLETED)
        self.assertEqual(list(ex2.records), ["s1", "s2", "s3", "s4"])

    def test_approval_records_approver_and_manual_done(self):
        book = approval_book()
        ex, _ = self.executor(book, ScriptedOperator([
            Decision("approve", "director-lisi", "批准"),
            Decision("done", "oncall-wangwu", "一致"),
        ]), name="h2")
        state = ex.run()
        self.assertIs(state.value, "completed")
        self.assertEqual(ex.records["s2"].decision_by, "director-lisi")
        self.assertEqual(ex.records["s2"].status, "ok")
        self.assertEqual(ex.records["s4"].status, "manual_done")
        self.assertEqual(ex.records["s4"].decision_by, "oncall-wangwu")

    def test_rejected_approval_fails_the_run(self):
        book = approval_book()
        ex, _ = self.executor(book, ScriptedOperator([
            Decision("reject", "director-lisi", "窗口取消"),
        ]), name="h3")
        state = ex.run()
        self.assertIs(state.value, "failed")
        self.assertEqual(ex.records["s2"].status, "rejected")
        self.assertNotIn("s3", ex.records)

    def test_pause_decision_keeps_waiting(self):
        step = Step("c", "确认", StepKind.CONFIRM, prompt="?")
        book = Runbook("rb-p", "暂停", [step])
        ex, _ = self.executor(book,
                              ScriptedOperator([Decision("pause", "z")]),
                              name="h4")
        self.assertIs(ex.run().value, "waiting_human")


class InterruptTests(RunbookTestBase):
    def test_interrupt_checkpoint_resume_and_abort(self):
        book = interrupt_book()
        ex, _ = self.executor(
            book, ScriptedOperator([Decision("approve", "z")]), name="i1",
            step_observer=lambda e, s:
                e.interrupt("test") if s.step_id == "s2" else None)
        state = ex.run()
        self.assertIs(state.value, "interrupted")
        ckpt = os.path.join(self.tmp, f"{ex.run_id}.checkpoint.json")
        self.assertTrue(os.path.exists(ckpt))
        self.assertEqual([r.step_id for r in ex.records.values()],
                         ["s1", "s2"])

        # 先冻结断点副本，供 4b 使用（4a 续跑会覆盖原检查点）
        abort_ckpt = os.path.join(self.tmp, "abort.copy.json")
        shutil.copy(ckpt, abort_ckpt)

        # 4a 续跑完成
        trace_a = TraceRecorder(os.path.join(self.tmp, "i1a.jsonl"),
                                ex.run_id, book.book_id)
        self.addCleanup(trace_a.close)
        exa = Executor.resume(
            ckpt, book,
            ScriptedOperator([Decision("approve", "z")]), trace_a)
        self.assertIs(exa.run().value, "completed")
        self.assertEqual(list(exa.records), ["s1", "s2", "s3", "s4"])

        # 4b 安全退出：从同一断点 abort，s3/s4 绝不执行
        trace_b = TraceRecorder(os.path.join(self.tmp, "i1b.jsonl"),
                                ex.run_id, book.book_id)
        self.addCleanup(trace_b.close)
        exb = Executor.resume(abort_ckpt, book, ScriptedOperator([]), trace_b)
        exb.abort("风险过高")
        self.assertIs(exb.run().value, "aborted")
        self.assertEqual([r.step_id for r in exb.records.values()],
                         ["s1", "s2"])

    def test_resume_does_not_reexecute_done_steps(self):
        calls = []

        def counting(step, ctx):
            calls.append(step.step_id)
            return {}

        book = Runbook("rb-c", "计数", [
            Step("a", "A", StepKind.AUTO, counting),
            Step("b", "B", StepKind.AUTO, counting),
        ])
        ex, _ = self.executor(book, ScriptedOperator([]), name="i2",
                              step_observer=lambda e, s:
                                  e.interrupt() if s.step_id == "a" else None)
        ex.run()
        ckpt = os.path.join(self.tmp, f"{ex.run_id}.checkpoint.json")
        trace2 = TraceRecorder(os.path.join(self.tmp, "i2.jsonl"),
                               ex.run_id, book.book_id)
        self.addCleanup(trace2.close)
        ex2 = Executor.resume(ckpt, book, ScriptedOperator([]), trace2)
        ex2.run()
        self.assertEqual(calls, ["a", "b"])  # a 不重复执行


class TakeoverTests(RunbookTestBase):
    def test_takeover_point_data_and_state(self):
        book = confirm_book()
        ex, _ = self.executor(book, ScriptedOperator([None]), name="t1")
        ex.run()
        point = ex.takeover("oncall-zhao")
        self.assertIs(ex.state.value, "human_controlled")
        self.assertEqual(point["current_step"], "s2")
        self.assertEqual([p["step_id"] for p in point["pending_steps"]],
                         ["s2", "s3", "s4"])
        self.assertEqual(point["state_before"], "waiting_human")
        self.assertEqual(point["state_after"], "human_controlled")
        self.assertIn("shared_data", point)
        self.assertTrue(os.path.exists(point["checkpoint_path"]))

    def test_takeover_forbidden_in_terminal_state(self):
        ex, _ = self.executor(full_auto_book(), AutoOperator(), name="t2")
        ex.run()
        with self.assertRaises(RuntimeError):
            ex.takeover("z")


class TraceTests(RunbookTestBase):
    def test_every_step_has_input_and_output_events(self):
        ex, trace = self.executor(full_auto_book(), AutoOperator(), name="tr")
        ex.run()
        events = read_trace(trace.path)
        kinds = [(e["step_id"], e["kind"]) for e in events]
        for sid in ("s1", "s2", "s3", "s4"):
            self.assertIn((sid, "step_input"), kinds)
            self.assertIn((sid, "step_output"), kinds)
        # 事件序号严格递增，可按时间核对
        seqs = [e["seq"] for e in events]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(seqs), len(set(seqs)))
        # 输入中带参数，输出中带处理器结果，可核对
        out = [e for e in events if e["kind"] == "step_output"
               and e["step_id"] == "s3"][0]
        self.assertTrue(out["payload"]["output"]["restarted"])

    def test_checkpoint_book_mismatch_detected(self):
        ex, _ = self.executor(full_auto_book(), AutoOperator(), name="tr2")
        ex.run()
        ckpt = os.path.join(self.tmp, f"{ex.run_id}.checkpoint.json")
        trace = TraceRecorder(os.path.join(self.tmp, "tr2b.jsonl"),
                              "x", "other-book")
        self.addCleanup(trace.close)
        other = Runbook("other-book", "别的手册", [])
        with self.assertRaises(ValueError):
            Executor.resume(ckpt, other, AutoOperator(), trace)


if __name__ == "__main__":
    unittest.main(verbosity=2)
