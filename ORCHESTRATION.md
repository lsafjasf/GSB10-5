# 值班手册自动化 — 编排说明

库：`runbook/`（Python 3，仅标准库）。核心原则：**需要人判断的步骤，执行器必须停下来等人，绝不自动往下冲**。

## 1. 步骤分类表

| 分类 | `StepKind` | 编排方式 | 执行器行为 |
|---|---|---|---|
| 自动步骤 | `AUTO` | 绑定处理器函数 `handler(step, ctx)` | 直接执行，记录输入/输出后继续 |
| 人工确认 | `CONFIRM` | 只给 `prompt`，不给 handler | **停下等待**人工 approve/reject/pause；人不在（返回 None）则落检查点停在 `waiting_human` |
| 审批 | `APPROVAL` | 只给 `prompt` | 同 CONFIRM，但决策必须带审批人身份（`decision_by` 留痕） |
| 人工执行 | `MANUAL` | 只给 `prompt` | 停下等待人工回报 `done`（状态记为 `manual_done`） |

手册编排示例（`runbooks.py`）：

```python
Runbook("rb-approval", "生产数据库重启", steps=[
    Step("s1", "预检查",       StepKind.AUTO, actions.check_service, {"service": "db"}),
    Step("s2", "值班主任审批", StepKind.APPROVAL, prompt="重启生产库需值班主任批准"),
    Step("s3", "重启数据库",   StepKind.AUTO, actions.restart_service, {"service": "db"}),
    Step("s4", "复核一致性",   StepKind.MANUAL, prompt="人工抽查 3 张核心表后回报 done"),
])
```

强制保证：
- 人工步骤的唯一前进路径是 `Operator` 返回明确决策；`Operator` 返回 `None`（人不在）时执行器落检查点并停在 `WAITING_HUMAN`（`runbook/executor.py` 的 `_run_gated`）。
- 声明全自动的手册配 `AutoOperator`，若手册里混入人工步骤直接报错，不靠自觉。

## 2. 执行轨迹（可核对）

每步产生 `step_input` / `step_output` 事件，JSONL 追加写入，字段：`seq`（严格递增）、`ts`（UTC）、`run_id`、`step_id`、`kind`、`payload`。
- `step_input`：步骤参数 + 当时的共享上下文快照。
- `step_output`：状态 + 处理器输出 / 人工决策（含决策人、备注）。
- 其他事件：`run_start`、`wait_human`、`human_decision`、`checkpoint`、`interrupt`、`resume`、`takeover`、`abort`、`run_end`。

核对方法：`runbook.trace.read_trace(path)` 或 `jq . artifacts/*/trace.jsonl`；
自测 `TraceTests` 验证每个步骤都有配对的输入/输出事件且序号唯一递增。

## 3. 人工接管

任何非终态都可调用 `Executor.takeover(operator_name)`，返回接管点数据并落检查点，状态转为 `HUMAN_CONTROLLED`。接管点数据（实例见 `artifacts/2_confirm_takeover/takeover_point.json`）：

| 字段 | 含义 |
|---|---|
| `state_before` / `state_after` | 接管前状态（如 `waiting_human`）→ `human_controlled` |
| `current_step` / `next_index` | 接管时停在哪一步 |
| `completed_steps` | 已完成步骤及其输出、决策人 |
| `pending_steps` | 剩余步骤（id/标题/分类） |
| `shared_data` | 跨步骤共享数据快照 |
| `checkpoint_path` | 现场文件，接管后可 `Executor.resume()` 交还执行器或 `abort()` 退出 |

## 4. 中断后的两种结果（演示产物 `artifacts/4_interrupt/`）

执行中被打断（`interrupt()` 或 SIGINT/SIGTERM）→ 在步骤边界落检查点，状态 `INTERRUPTED`（断点在 `s3` 前，`s1`、`s2` 已完成）：

- **续跑**：`Executor.resume(checkpoint, ...)` 从断点继续，已完成步骤不重复执行（自测 `test_resume_does_not_reexecute_done_steps` 验证），最终 `completed`，步骤记录 `s1..s4` 完整。
- **安全退出**：从同一断点 `abort()`，后续步骤 `s3`/`s4` 绝不执行，状态收敛 `ABORTED`，已执行步骤仅为 `s1`、`s2`（演示 `safe_abort.executed_steps` 可核对）。

检查点用「临时文件 + 原子替换」写入，防止半写；`resume` 时校验手册 id，拿错手册的检查点直接报错。

## 5. 性能（1000 本手册 × 10 步 = 10,000 步）

实测（`artifacts/benchmark/benchmark.json`，Python 3.12.3 / Linux，含轨迹落盘与每本手册检查点）：

| 指标 | 数值 |
|---|---|
| 总耗时 | 1.39 s |
| 吞吐 | ≈ 7,195 步/秒 |
| 单本手册平均 | 1.39 ms |
| Python 堆内存峰值（tracemalloc） | 0.15 MB |
| 进程常驻内存峰值（maxrss） | 19.16 MB |
| 最终状态 | 1000/1000 `completed` |

复现：`python3 benchmark.py`（可用 `--books/--steps` 调整规模）。

## 6. 运行命令

```bash
python3 test_runbook.py     # 自测（12 例：全自动/确认/审批/拒绝/中断续跑/安全退出/接管/轨迹）
python3 demo.py             # 四种情形演示，产物在 artifacts/
python3 benchmark.py        # 性能基准，结果在 artifacts/benchmark/benchmark.json
```

真实值班接入：把手册换成 `ConsoleOperator`（阻塞等待真人输入），并给 `Executor` 传 `install_signal_handlers=True` 即可响应 Ctrl-C/SIGTERM 安全中断。
