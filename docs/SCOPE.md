# 回滚演练范围说明

## 1. 演练目标

验证「数据回滚到已知良好快照」这条预案在当前系统上**真的可执行**：不是检查文档、
不是跑 mock，而是按生产回滚的同一份代码、同一套步骤，在生产数据的逐字节副本上
完成一次端到端回滚，并对每一步给出可判定的数据。

## 2. 演练范围（IN SCOPE）

- **走真实流程**：演练与真实回滚调用同一个执行器 `DrillRunner.run`，
  执行同一份冻结步骤表 `build_steps()`（10 步，ID/顺序固定）。
  不存在"演练专用分支"，二者唯一差异是 `Plan` 绑定（模式标签、目标目录、审计文件）。
  对照数据见 `docs/PATH_PARITY.md`。
- **用真实数据副本**：演练前用 `FileEnv.clone_to()` 对生产目录做逐字节全量拷贝
  （`shutil.copytree` + SHA-256 校验），演练全程只写克隆，不触碰生产目录。
  步骤 4「安全副本捕获」和步骤 5「执行恢复」都是对真实文件的全量复制/覆盖，
  不是内存模拟。
- **覆盖步骤**：

  | # | step_id | 步骤 | 判定数据 |
  |---|---------|------|----------|
  | 1 | s01_precheck | 资源预检 | 文件数、数据字节数 |
  | 2 | s02_verify_snapshot | 快照完整性 | manifest 指纹 == 实际快照 SHA-256 |
  | 3 | s03_fence_writes | 写入围栏 | 围栏状态、围栏时写入水位线 |
  | 4 | s04_capture_safety | 安全副本捕获 | 副本 SHA-256 == 当前 live SHA-256 |
  | 5 | s05_restore | 执行恢复 | 恢复前后 SHA-256 必须变化 |
  | 6 | s06_verify_restored | 恢复内容校验 | live SHA-256 == 快照 SHA-256 |
  | 7 | s07_drift_check | 数据漂移检测 | 水位线 == 围栏时基线（无新增写入） |
  | 8 | s08_unfence | 解除围栏 | 围栏状态 == open |
  | 9 | s09_smoke | 冒烟测试 | 全部文件可读、无空文件 |
  | 10 | s10_audit_commit | 审计落账 | JSONL 写入 COMMITTED |

## 3. 范围依据

- **路径必须一致**：演练最大的失效模式是"演练能过、真回滚过不了"。因此步骤实现
  （`target.py` 中 `step_s01..s10`）与执行循环只写一份；`Plan.mode` 仅是绑定参数，
  不产生分支。路径一致性本身由自测 `test_drill_and_real_walk_identical_path` 与
  `path_parity.compare_runs()` 逐步骤机器校验。
- **先围栏再动手**：回滚期间任何并发写入都会造成数据撕裂。步骤 3 先置围栏并记录
  水位线，步骤 7 用同一水位线检测"绕过围栏的新写入"（中途有新写入情形）。
- **恢复前先安全副本**：步骤 4 在覆盖 live 前做全量安全副本并校验指纹一致；
  一旦步骤 5/6/7 失败，确定处置是把安全副本原样恢复，回到回滚前状态，
  而不是停在"恢复了一半"的中间态。
- **逐步给数据而不是给成败结论**：每步产出 1..N 个 `Check(name, expected, actual, passed)`，
  报告（`reports/drill_*.md`）逐行列出期望值、实际值与 ✓/✗。
- **时间由测试注入**：执行器不直接读系统时钟，只接受 `Clock` 协议对象；
  生产用 `SystemClock`，自测用 `FakeClock`，因此超时、周期、漂移窗口全部可确定性复现。

## 4. 不在范围（OUT OF SCOPE）

- 跨机房/跨地域的网络层回滚、应用进程发布回滚（本库只覆盖数据面）。
- 对真实生产流量的干扰测试；新写入场景由故障注入 `FaultPlan.bypass_after` 确定性模拟。

## 5. 部分失败时的确定处置

| 失败步骤 | 策略 | 确定结果 |
|----------|------|----------|
| s01 资源预检 / s02 快照校验 | 中止 ABORT | 后续全部 SKIPPED；围栏未建立，live 未改动 |
| s03 围栏 / s04 安全副本 | 中止 + 解除围栏 | 后续 SKIPPED；best-effort 解除围栏，live 未改动 |
| s05 恢复 / s06 内容校验 / s07 漂移检测 | 回退 ROLLBACK | 后续 SKIPPED；用安全副本恢复 live（指纹回到回滚前），围栏保持关闭等待人工 |
| s08 解除围栏 / s09 冒烟 / s10 审计 | 人工标记 MARK | 后续 SKIPPED；数据已是快照版本，打 MANUAL_* 标记等待人工决策，不自动放行 |
| 任意步骤超时 | 按该步骤策略中止/回退 | 标记 timeout 与阈值；恢复/校验类超时按 ROLLBACK 回退 |

每次中止都会对未执行步骤显式写 SKIPPED（共 10 行结果），补偿成败单独记录
（`compensated` / `compensation_detail`），审计 JSONL 同步落 `STEP_FAILED`、
`COMPENSATED`、`MANUAL_MARK`、`STEP_TIMEOUT` 事件。

## 6. 覆盖情形与对应自测

| 情形 | 自测 | 样例数据 |
|------|------|----------|
| 全部成功 | `TestAllSuccess` | `reports/drill_success.md` |
| 单步失败（10 步逐一） | `TestSingleStepFailure` | `reports/drill_step_failure.md` |
| 中途有新写入（漂移） | `TestMidRunWrites` | `reports/drill_drift.md` |
| 演练超时（单步+总时限） | `TestTimeout` | `reports/drill_timeout.md` |
| 定期调度 | `TestScheduler` | — |
| 100 次耗时/资源 | `TestHundredRuns` + `tools/benchmark.py` | `docs/PERF.md`, `reports/benchmark_100.csv` |

## 7. 定期验证

`DrillScheduler` 按固定间隔（默认每周 7 天，参数 `interval_s`）触发，
时钟注入决定到期判断；到期未演练前 `due()` 返回 True。建议在 CI/定时任务中
每周运行一次 `python3 -m unittest discover -s tests` 与 `tools/benchmark.py`，
任何一步失败即整个演练失败（退出码非 0）。
