# 数据保留期清理 — 判定说明

## 记录字段

| 字段 | 含义 |
|---|---|
| `id` | 记录唯一标识 |
| `created_at` | 创建时间（epoch 秒） |
| `last_accessed_at` | 最后访问时间（epoch 秒） |
| `references` | 本记录引用的其他记录 id 列表 |
| `retention_hold` | 暂停保留标记（True = 暂停清理） |

当前时间 `now` 一律由调用方注入，库内不读系统时钟，判定可离线重放、可测试。

## 判定规则（按优先级，命中即停）

1. **时间异常（KEEP_CLOCK_ANOMALY）**
   - 任一时间戳落在 `(now + tolerance, +∞)`，即时钟严重不准（未来时间超出容忍带）；或
   - `last_accessed_at < created_at`，时间戳自相矛盾。
   - 处理：**保守保留，绝不删除**，并记入异常清单。容忍带内（默认 300s）的轻微未来时间戳钳制为 `now`，视为“刚刚活动过”，不算异常。
2. **暂停保留（KEEP_HOLD）**：`retention_hold == True`，无论多旧都保留。
3. **被引用（KEEP_REFERENCED）**：被任意一条**现存**记录引用即保留。即使引用方本轮自己也会被删，被引记录本轮仍幸免（避免级联误删，下一轮自然收敛）。
4. **到期判定**：
   - 最后活动时间 = `max(created_at, last_accessed_at)`；
   - `now - 最后活动时间 >= retention_seconds` → **删除（DELETE_EXPIRED）**；
   - 否则 → 保留（KEEP_NOT_EXPIRED）。

## 引用完整性校验（删除前执行）

- 先扫描全部记录的 `references`，目标 id 不存在的记为**悬空引用**（`dangling_references`），写入汇总报告。
- 悬空引用**不构成保护**：指向不存在目标的记录照常参与到期判定。
- 只有引用方记录当前存在，其引用才保护目标记录。

## 时钟不准与暂停保留下的结果

| 情形 | 结果 |
|---|---|
| 时间戳超出容忍带的未来（时钟严重前拨） | 判为异常，保守保留，进异常清单 |
| `last_accessed_at < created_at` | 判为异常，保守保留，进异常清单 |
| 时间戳在容忍带内的未来（轻微前拨） | 钳制为 `now`，按未到期保留 |
| 系统时钟整体回拨（所有记录看似来自未来） | 全部按异常/未到期保留，**零删除** |
| `retention_hold = True` | 无论多旧、是否到期，一律保留 |

原则：时钟不可信时宁可不删，也绝不误删。

## 可追溯性

- `deletion_manifest`：每条被删记录含 `id / reason / created_at / last_accessed_at / idle_seconds / evaluated_at`。
- `spared_list`：每条幸免记录含 `id / decision / reason / referrers`（被引用幸免者附引用方清单）。
- `summary`：各判定类别计数 + 悬空引用明细。
- 评估与删除分离：`evaluate()` 只出判定报告，调用方审计后再执行删除。

## 性能（100 万条记录，实测）

环境：Python 3，标准库，单线程。判定耗时约 **10 秒**，`tracemalloc` 峰值约 **220 MB**，进程峰值 RSS 约 **695 MB**。明细见 `out/benchmark_1m.json`，可用 `python -m retention_cleanup.benchmark` 复现。
