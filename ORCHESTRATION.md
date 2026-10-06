# 值班手册自动化执行 — 编排说明

纯 Python 3 标准库实现，零第三方依赖。核心库 `runbook.py`，自测 `selftest.py`，
演示 `demo.py`，压测 `bench.py`。

## 1. 步骤编排方式

手册（`Runbook`）= 有序步骤（`Step`）列表 + 共享上下文（`context`）。
执行器（`Executor`）按顺序驱动；每步完成后立即原子落检查点（tmp + rename），
因此任何时刻崩溃，状态都停在最近一个完整步骤上。

```python
Runbook("发布手册", [
    Step("s1", "预检",         StepKind.AUTO,     "check_service", {"name": "web"}),
    Step("s2", "确认流量切走", StepKind.CONFIRM),                    # 必须等人
    Step("s3", "DBA 审批",     StepKind.APPROVAL),                   # 必须等审批
    Step("s4", "重启服务",     StepKind.AUTO,     "restart_service", {"name": "web"}),
], context={"env": "prod"})
```

人工步骤的决策由 `DecisionProvider` 注入：返回 `Decision` 表示人已拍板；
返回 `None` 表示没人拍板 —— **执行器立即暂停落盘，绝不自行往下冲**。

## 2. 步骤分类表

| 分类 | kind | 谁来推进 | 执行器行为 | 典型场景 |
|---|---|---|---|---|
| 全自动 | `auto` | 执行器 | 直接调用注册动作，记录输入/输出 | 健康检查、重启、通知 |
| 人工确认 | `confirm` | 值班人 | 发出 `decision_requested` 后暂停等待；拒绝则 `aborted` | 切流确认、破坏性操作前确认 |
| 审批 | `approval` | 审批人(如 DBA/经理) | 同上，但拒绝进入 `rejected` 终态 | 变更窗口、生产库操作 |

## 3. 执行轨迹（可核对）

`trace.jsonl` 追加写，每行一条记录：`seq / ts / run_id / event / step_id /
payload / prev_hash / hash`。`hash = sha256(本条除 hash 外的规范 JSON)`，
`prev_hash` 串联上一条 —— 改任何一行都会断链。

- 每步有 `step_started`（含 `inputs`）与 `step_finished`（含 `outputs`）配对，逐条可核对。
- 人工步骤额外有 `decision_requested` / `decision_received`（含决策人与备注）。
- 核对命令：`python3 -c "from runbook import verify_trace; print(verify_trace('artifacts/1_full_auto/trace.jsonl'))"`

事件全集：`run_started, step_started, step_finished, step_failed,
decision_requested, decision_received, run_paused, run_resumed,
run_completed, run_failed, run_rejected, run_aborted, run_interrupted, takeover`。

## 4. 人工接管

任何非终态时刻都可 `executor.takeover(run_id, operator)`：

- 返回接管点数据：当前步骤下标、待办步骤、剩余步骤、各步状态、上下文快照、
  已有决策、检查点路径、轨迹序号（见 `artifacts/5_takeover/takeover_point.json`）。
- 接管后运行状态置为 `manual`，执行器对该运行拒绝 `resume`（抛错），
  防止人机双头驾驶。

## 5. 中断后的两种结果

执行到一半被打断（Ctrl-C / 进程被杀）后，检查点保留在最近完成的步骤：

- **续跑**：`executor.resume(run_id)` 从断点继续，直至 `completed`
  （见 `artifacts/4_interrupted`，`resume=completed`）。
- **安全退出**：`executor.abort(run_id, reason)` 置 `aborted` 终态，
  后续步骤一个都不会再跑（同目录，`safe_exit=aborted`）。

注意：动作按至少一次语义重放（断点所在步骤可能重跑），动作实现需幂等。

## 6. 性能（1000 本手册 × 10 步 = 10000 步）

实测数据见 `artifacts/benchmark.json`（本机 Python 3.12.3）：

| 指标 | 数值 |
|---|---|
| 总耗时 | ~7.3 s（含每步检查点落盘 + 轨迹哈希链） |
| 吞吐 | ~1370 步/秒 |
| 平均每步 | ~0.73 ms |
| Python 堆峰值 | ~0.4 MB |
| 进程 RSS 峰值 | ~34 MB |

耗时主要在每步的原子落盘（durability 换时间）；内存与手册数量无关，
因为状态逐本落盘、不驻留。

## 7. 运行命令

```bash
python3 selftest.py    # 11 个自测：四类情形 + 轨迹核对 + 接管
python3 demo.py        # 生成 artifacts/ 下五个场景的执行轨迹与接管点数据
python3 bench.py       # 压测 1000 手册 / 10000 步，写 artifacts/benchmark.json
```
