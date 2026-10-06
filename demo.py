"""四种情形演示，产物全部落在 artifacts/ 下，便于核对。

  1. 全自动手册            -> completed
  2. 含人工确认            -> 在确认点停下 -> 人工接管 -> 续跑完成
  3. 需要审批 + 人工执行    -> 审批通过、手工回报后完成
  4. 执行中被打断          -> 4a 检查点续跑完成 / 4b 安全退出 ABORTED
"""
from __future__ import annotations

import json
import os
import shutil

from runbook import (
    AutoOperator, ConsoleOperator, Decision, Executor, ScriptedOperator,
    TraceRecorder,
)
from runbooks import approval_book, confirm_book, full_auto_book, interrupt_book

ART = os.path.join(os.path.dirname(os.path.abspath(__file__)), "artifacts")


def fresh_dir(path: str) -> str:
    if os.path.exists(path):
        shutil.rmtree(path)
    os.makedirs(path, exist_ok=True)
    return path


def scenario_full_auto() -> dict:
    work = fresh_dir(os.path.join(ART, "1_full_auto"))
    book = full_auto_book()
    trace = TraceRecorder(os.path.join(work, "trace.jsonl"), "demo-1", book.book_id)
    ex = Executor(book, AutoOperator(), trace, checkpoint_dir=work)
    state = ex.run()
    trace.close()
    return {"scenario": "full_auto", "final_state": state.value,
            "run_id": ex.run_id,
            "trace": os.path.join(work, "trace.jsonl"),
            "checkpoint": ex.checkpoint_path}


def scenario_confirm_and_takeover() -> dict:
    work = fresh_dir(os.path.join(ART, "2_confirm_takeover"))
    book = confirm_book()
    # 第一个确认点"人不在"（None）：执行器必须停下，不能自己往下冲
    trace = TraceRecorder(os.path.join(work, "trace.jsonl"), "demo-2", book.book_id)
    ex = Executor(book, ScriptedOperator([None]), trace, checkpoint_dir=work)
    state1 = ex.run()
    assert state1.value == "waiting_human", state1

    # 值班员接管，导出接管点数据
    point = ex.takeover(operator_name="oncall-zhangsan")
    with open(os.path.join(work, "takeover_point.json"), "w", encoding="utf-8") as fh:
        json.dump(point, fh, ensure_ascii=False, indent=2)

    # 接管后状态已落检查点；新进程/新执行器从检查点续跑，两次确认都批准
    trace2 = TraceRecorder(os.path.join(work, "trace.jsonl"), "demo-2", book.book_id)
    op2 = ScriptedOperator([Decision("approve", "zhangsan", "低峰已确认"),
                            Decision("approve", "zhangsan", "IO 正常")])
    ex2 = Executor.resume(point["checkpoint_path"], book, op2, trace2)
    state2 = ex2.run()
    trace.close()
    trace2.close()
    return {"scenario": "confirm_takeover",
            "state_when_stopped": state1.value,
            "state_after_takeover": point["state_after"],
            "current_step_at_takeover": point["current_step"],
            "final_state": state2.value,
            "run_id": ex2.run_id,
            "trace": os.path.join(work, "trace.jsonl"),
            "takeover_point": os.path.join(work, "takeover_point.json")}


def scenario_approval() -> dict:
    work = fresh_dir(os.path.join(ART, "3_approval"))
    book = approval_book()
    trace = TraceRecorder(os.path.join(work, "trace.jsonl"), "demo-3", book.book_id)
    op = ScriptedOperator([
        Decision("approve", "director-lisi", "维护窗口内，批准重启"),
        Decision("done", "oncall-wangwu", "三表抽查一致"),
    ])
    ex = Executor(book, op, trace, checkpoint_dir=work)
    state = ex.run()
    trace.close()
    return {"scenario": "approval", "final_state": state.value,
            "run_id": ex.run_id,
            "trace": os.path.join(work, "trace.jsonl")}


def scenario_interrupt() -> dict:
    work = fresh_dir(os.path.join(ART, "4_interrupt"))
    book = interrupt_book()

    # --- 第一次执行：s2 完成后由观察钩子注入中断 ---
    trace = TraceRecorder(os.path.join(work, "trace.jsonl"), "demo-4", book.book_id)
    ex = Executor(
        book, ScriptedOperator([Decision("approve", "zhangsan")]),
        trace, checkpoint_dir=work,
        step_observer=lambda e, s: e.interrupt("demo-inject") if s.step_id == "s2" else None,
    )
    state1 = ex.run()
    assert state1.value == "interrupted", state1
    ckpt = ex.checkpoint_path
    trace.close()

    # 先冻结断点副本供 4b 使用（4a 续跑会覆盖原检查点）
    ckpt_abort = os.path.join(work, "abort.copy.json")
    shutil.copy(ckpt, ckpt_abort)

    # --- 4a：从检查点续跑到完成 ---
    trace_a = TraceRecorder(os.path.join(work, "trace_resume.jsonl"),
                            ex.run_id, book.book_id)
    ex_a = Executor.resume(ckpt, book,
                           ScriptedOperator([Decision("approve", "zhangsan",
                                                      "指标正常")]),
                           trace_a)
    state_a = ex_a.run()
    trace_a.close()

    # --- 4b：另起一次，从同一断点选择"安全退出"，不再执行后续步骤 ---
    trace_b = TraceRecorder(os.path.join(work, "trace_abort.jsonl"),
                            ex.run_id, book.book_id)
    ex_b = Executor.resume(ckpt_abort, book, ScriptedOperator([]), trace_b)
    ex_b.abort("值班员判断风险过高，放弃发布")
    state_b = ex_b.run()
    executed_after_abort = [
        r.step_id for r in ex_b.records.values()
    ]  # 只有断点前的步骤，不应出现 s3/s4
    trace_b.close()

    return {"scenario": "interrupt",
            "state_when_interrupted": state1.value,
            "interrupted_next_step": "s3",
            "resume": {"final_state": state_a.value},
            "safe_abort": {"final_state": state_b.value,
                           "executed_steps": executed_after_abort},
            "checkpoint": ckpt}


def main() -> None:
    results = [
        scenario_full_auto(),
        scenario_confirm_and_takeover(),
        scenario_approval(),
        scenario_interrupt(),
    ]
    out = os.path.join(ART, "scenario_summary.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"\n产物目录: {ART}")


if __name__ == "__main__":
    main()
