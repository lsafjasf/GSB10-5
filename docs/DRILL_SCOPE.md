# 回滚演练定期验证 — 范围说明

## 为什么写在文档里不等于能用

回滚方案是"冷代码"：依赖的备份会过期、数据格式会漂移、权限会变化。
只有**定期按真实路径完整走一遍**，才能在出事时相信它。本库把演练做成
可自动运行、可逐步判定、可与真实回滚逐步对照的代码，而不是一份文档。

## 演练范围

| 维度 | 范围 | 依据 |
|---|---|---|
| 流程 | 走**真实回滚的同一函数** `run_pipeline`，步骤序列 `STEP_SPECS` 完全一致 | 路径不同则演练证明不了真实回滚可用；见 `data/path_comparison.json` |
| 数据 | 使用**线上数据的真实副本**（`DataStore.clone` → `drill-sandbox`），恢复校验用真实备份的校验和 | 用假数据测不出备份损坏、格式漂移；用线上库本身则演练本身成了事故 |
| 步骤 | 覆盖回滚全链路 8 步：快照 → 校验备份 → 冻结写入 → 恢复备份 → 校验恢复 → 检测新写入 → 解冻 → 清理 | 任何一步缺失，该步就是未验证的盲区 |
| 线上影响 | 演练只读线上库（记录写水位线），恢复只落在沙箱副本，结束即丢弃 | 演练绝不能成为新的故障源；由 `test_drill_does_not_touch_production` 保证 |

## 与真实回滚的步骤对照

两者执行**同一份步骤代码**，差异仅在运行参数（3 步有参数差异）：

| 步骤 | 真实回滚 | 演练 |
|---|---|---|
| freeze_writes | 硬冻结线上写入 | 只记录写水位线 |
| restore_backup | 恢复到线上库 | 恢复到线上数据副本（沙箱） |
| cleanup | 无沙箱清理 | 丢弃沙箱副本 |

逐步对照数据见 `data/path_comparison.json`（`path_identical: true`）。

## 逐步判定

每步输出 `status / started_at / ended_at / duration / data / error`
（见 `data/scenario_results.json`），整体判定由各步结果推导，
**不存在只有一个成功/失败的总判定**。

## 部分失败的确定处理（POLICY）

| 情形 | 策略 | 行为 |
|---|---|---|
| 任一步骤失败 | `abort_and_compensate` | 立即中止，后续步骤标记 skipped，已完成的 freeze/restore 逆序补偿（先清沙箱、再解冻） |
| 演练期间有新写入 | `mark_and_abort` | 报告标记 `tainted=true`、结果判 `invalidated`（本次演练不可信，需重跑），中止并补偿 |
| 超过截止时间 | `abort_and_compensate` | 判定 `timeout`，中止并补偿 |

## 定期运行

时间由调用方注入（`ManualClock`），调度用系统 cron 即可，例如每周一次：

```cron
0 3 * * 1  cd /home/administrator/gsb/uid450/A && python3 -m rollback_drill.report --out data
```

## 运行命令

```bash
python3 -m unittest discover -s tests -v   # 自测（9 个用例，覆盖全部要求情形）
python3 -m rollback_drill.report --out data # 生成逐步结果/路径对照/100 次耗时数据
```
