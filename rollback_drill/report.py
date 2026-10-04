"""Markdown rendering of per-run step-by-step results."""

from .model import RunResult


def render_run(result: RunResult) -> str:
    lines = [
        f"# 回滚演练报告 `{result.run_id}`",
        "",
        f"- 模式: `{result.mode.value}`",
        f"- 计划: `{result.plan_id}` (签名 `{result.plan_signature}`)",
        f"- 总体判定: **{result.overall.value}**",
        f"- 总耗时: {result.total_ms:.3f} ms",
        f"- 资源: {result.resources}",
        "",
        "| # | step_id | 步骤 | 状态 | 耗时ms | 判定数据 | 补偿/标记 | 错误 |",
        "|---|---------|------|------|--------|----------|-----------|------|",
    ]
    for s in result.steps:
        checks = "<br>".join(
            f"{'✓' if c.passed else '✗'} {c.name}: 期望 `{c.expected}` 实际 `{c.actual}`"
            for c in s.checks
        ) or "-"
        err = s.error or "-"
        if s.compensated:
            action = f"已回退: `{s.compensation_detail}`"
        elif s.compensation_detail:
            action = f"回退失败: `{s.compensation_detail}`"
        elif "manual_mark" in s.detail:
            action = f"人工标记: `{s.detail['manual_mark']}`"
        else:
            action = "-"
        lines.append(
            f"| {s.index} | {s.step_id} | {s.name} | {s.status.value} | "
            f"{s.duration_ms:.3f} | {checks} | {action} | {err} |"
        )
    if result.marked_for_manual:
        lines += ["", f"> 已打人工标记: `{result.mark_reason}`"]
    if result.timeout:
        lines += ["", f"> 触发超时，阈值 {result.timeout_after_ms} ms"]
    return "\n".join(lines) + "\n"
