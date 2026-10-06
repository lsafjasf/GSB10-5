"""演示四类情形，把执行轨迹与接管点数据写入 artifacts/ 作为交付物。"""
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from runbook import (ACTIONS, Decision, Executor, Runbook, Step, StepKind,
                     action, verify_trace)

ART = Path(__file__).parent / "artifacts"


@action("check_service")
def _check(params, ctx):
    ctx["service_healthy"] = True
    return {"service": params["name"], "healthy": True, "latency_ms": 12}


@action("restart_service")
def _restart(params, ctx):
    return {"service": params["name"], "restarted": True, "pid": 4242}


@action("notify")
def _notify(params, ctx):
    return {"channel": params["channel"], "sent": True}


def fresh_executor(name):
    d = ART / name
    shutil.rmtree(d, ignore_errors=True)
    return Executor(d / "checkpoints", d / "trace.jsonl"), d


def scenario_full_auto():
    ex, d = fresh_executor("1_full_auto")
    rb = Runbook("磁盘清理手册", [
        Step("s1", "检查服务健康", StepKind.AUTO, "check_service", {"name": "api"}),
        Step("s2", "重启服务", StepKind.AUTO, "restart_service", {"name": "api"}),
        Step("s3", "群里通知", StepKind.AUTO, "notify", {"channel": "oncall"}),
    ], {"env": "prod"})
    st = ex.start(rb)
    return d, st["status"]


def scenario_confirm():
    ex, d = fresh_executor("2_manual_confirm")
    rb = Runbook("发布手册", [
        Step("s1", "预检", StepKind.AUTO, "check_service", {"name": "web"}),
        Step("s2", "确认流量已切走", StepKind.CONFIRM),
        Step("s3", "重启服务", StepKind.AUTO, "restart_service", {"name": "web"}),
    ])
    st = ex.start(rb)  # 无人确认 -> 暂停
    assert st["status"] == "paused", st["status"]
    st = ex.resume(st["run_id"], lambda s, c: Decision(True, "值班-张三", "流量已切"))
    return d, st["status"]


def scenario_approval():
    ex, d = fresh_executor("3_approval")
    rb = Runbook("数据库变更手册", [
        Step("s1", "备份检查", StepKind.AUTO, "check_service", {"name": "db"}),
        Step("s2", "DBA 审批", StepKind.APPROVAL),
        Step("s3", "执行变更", StepKind.AUTO, "restart_service", {"name": "db"}),
    ])
    st = ex.start(rb, lambda s, c: Decision(False, "dba-李四", "不在变更窗口"))
    assert st["status"] == "rejected"
    # 第二次：审批通过
    rb2 = Runbook("数据库变更手册(重提)", rb.steps)
    st2 = ex.start(rb2, lambda s, c: Decision(True, "dba-李四", "窗口内，放行"))
    return d, f"{st['status']} -> {st2['status']}"


def scenario_interrupt():
    ex, d = fresh_executor("4_interrupted")

    @action("wait_ctrl_c")
    def _hang(params, ctx):
        raise KeyboardInterrupt  # 模拟执行到一半被 Ctrl-C

    rb = Runbook("长任务手册", [
        Step("s1", "步骤一", StepKind.AUTO, "check_service", {"name": "job"}),
        Step("s2", "步骤二(被打断)", StepKind.AUTO, "wait_ctrl_c"),
        Step("s3", "步骤三", StepKind.AUTO, "notify", {"channel": "oncall"}),
    ])
    st = ex.start(rb)
    assert st["status"] == "interrupted", st["status"]

    # 情形 A：续跑 —— 障碍排除后从断点继续到完成
    ACTIONS["wait_ctrl_c"] = lambda p, c: {"resumed": True}
    resumed = ex.resume(st["run_id"])

    # 情形 B：安全退出 —— 另起一次运行，打断后 abort
    ACTIONS.pop("wait_ctrl_c")
    @action("wait_ctrl_c")
    def _hang2(params, ctx):
        raise KeyboardInterrupt
    st2 = ex.start(rb)
    aborted = ex.abort(st2["run_id"], "值班人决定改期执行", operator="值班-王五")
    return d, f"resume={resumed['status']}, safe_exit={aborted['status']}"


def scenario_takeover():
    ex, d = fresh_executor("5_takeover")
    rb = Runbook("故障处置手册", [
        Step("s1", "自动诊断", StepKind.AUTO, "check_service", {"name": "pay"}),
        Step("s2", "确认是否熔断", StepKind.CONFIRM),
        Step("s3", "执行熔断", StepKind.AUTO, "restart_service", {"name": "pay"}),
    ], {"env": "prod", "incident": "INC-1024"})
    st = ex.start(rb)  # 停在确认步
    point = ex.takeover(st["run_id"], operator="资深值班-赵六")
    (d / "takeover_point.json").write_text(
        json.dumps(point, ensure_ascii=False, indent=2), encoding="utf-8")
    return d, point["status_after"]


def main():
    ART.mkdir(exist_ok=True)
    results = {}
    for fn in (scenario_full_auto, scenario_confirm, scenario_approval,
               scenario_interrupt, scenario_takeover):
        d, status = fn()
        ok, problems = verify_trace(d / "trace.jsonl")
        assert ok, problems
        results[d.name] = status
        print(f"[{d.name}] status={status}  trace=OK ({d / 'trace.jsonl'})")
    (ART / "summary.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("全部轨迹哈希链核对通过，产物见 artifacts/")


if __name__ == "__main__":
    main()
