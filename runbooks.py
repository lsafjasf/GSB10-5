"""四本样例手册：全自动 / 含人工确认 / 需要审批 / 执行中被打断。"""
from runbook import Runbook, Step, StepKind
from runbook import actions


def full_auto_book() -> Runbook:
    return Runbook(
        book_id="rb-auto",
        name="全自动手册：缓存清理",
        steps=[
            Step("s1", "记录开始", StepKind.AUTO, actions.log_message,
                 {"message": "开始清理缓存"}),
            Step("s2", "检查缓存服务", StepKind.AUTO, actions.check_service,
                 {"service": "cache"}),
            Step("s3", "重启缓存服务", StepKind.AUTO, actions.restart_service,
                 {"service": "cache"}),
            Step("s4", "复查健康", StepKind.AUTO, actions.check_service,
                 {"service": "cache"}),
        ],
    )


def confirm_book() -> Runbook:
    return Runbook(
        book_id="rb-confirm",
        name="含人工确认手册：磁盘扩容",
        steps=[
            Step("s1", "检查磁盘水位", StepKind.AUTO, actions.check_service,
                 {"service": "disk"}),
            Step("s2", "确认可下线扩容", StepKind.CONFIRM,
                 prompt="确认业务低峰、可执行磁盘扩容？"),
            Step("s3", "执行扩容", StepKind.AUTO, actions.restart_service,
                 {"service": "disk"}),
            Step("s4", "确认扩容结果", StepKind.CONFIRM,
                 prompt="容量与 IO 是否正常？"),
        ],
    )


def approval_book() -> Runbook:
    return Runbook(
        book_id="rb-approval",
        name="需审批手册：生产数据库重启",
        steps=[
            Step("s1", "预检查", StepKind.AUTO, actions.check_service,
                 {"service": "db"}),
            Step("s2", "值班主任审批", StepKind.APPROVAL,
                 prompt="重启生产库需值班主任批准"),
            Step("s3", "重启数据库", StepKind.AUTO, actions.restart_service,
                 {"service": "db"}),
            Step("s4", "复核数据一致性", StepKind.MANUAL,
                 prompt="人工抽查 3 张核心表后回报 done"),
        ],
    )


def interrupt_book() -> Runbook:
    """用于演示打断：s2 的处理器会请求中断。"""
    def trigger_interrupt(step, ctx):
        # 演示：模拟外部中断信号到达（真实场景由 signal handler 调用）
        ctx["data"]["__interrupt__"] = True
        return {"interrupted_at": step.step_id}

    return Runbook(
        book_id="rb-interrupt",
        name="被打断手册：分批发布",
        steps=[
            Step("s1", "发布第一批", StepKind.AUTO, actions.restart_service,
                 {"service": "app-batch-1"}),
            Step("s2", "观察指标(中断注入)", StepKind.AUTO, trigger_interrupt),
            Step("s3", "发布第二批", StepKind.AUTO, actions.restart_service,
                 {"service": "app-batch-2"}),
            Step("s4", "发布确认", StepKind.CONFIRM,
                 prompt="全部批次指标正常？"),
        ],
    )
