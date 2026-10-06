# 数据保留期自动清理（retention_cleanup）

纯 Python 3 标准库实现，无第三方依赖。当前时间一律由调用方/测试注入，
引擎自身绝不读取系统时钟（有专门测试守护这一点）。

## 文件

- `engine.py` — 清理引擎（判定、引用完整性校验、可追溯报告）
- `test_retention.py` — 26 个自测（unittest）
- `demo.py` — 确定性演示，输出幸免/清理清单并写 JSON 清单到 `outputs/`
- `benchmark.py` — 100 万条记录基准（耗时 + 内存峰值）
- `outputs/` — 演示清单、测试与基准的原始输出

## 判定说明

记录字段：`record_id`、`created_at`、`last_accessed_at`（可空）、
`referenced_by`（引用者 id 元组）、`hold`（暂停保留标记）。
策略参数：`retention_seconds`（空闲保留期）、`grace_seconds`（宽限）、
`skew_tolerance_seconds`（未来时间戳容忍）。

对每条记录，按以下优先级判定（前者优先）：

1. **时间异常 → 保留（anomaly）**：`created_at` 非有限数/为负/超过
   `now + skew_tolerance`；或显式给出的 `last_accessed_at` 非有限数、
   早于 `created_at`、超过容忍的未来。异常记录永不删除，转人工复核。
2. **被引用 → 保留（referenced）**：存在来自现存记录的引用（含自引用、
   互引环）。保守策略，不做级联删除。
3. **暂停标记 → 保留（held）**：`hold=True` 时保留期暂停计时，永不删除；
   解除标记后按正常规则重新判定。
4. **未到期 → 保留（active）**：`idle = now - max(created_at, last_accessed_at)`
   小于 `retention + grace`。
5. **其余 → 删除（delete）**：空闲超期、无引用、无暂停、无异常，
   理由码 `deleted:idle_expired`。

### 时钟不准的处理

- **时钟回拨（now 偏小）**：空闲期被低估，到期记录全部转为保留——
  宁可不删，绝不误删（场景 B 演示 + `test_regressed_clock_keeps_everything`）。
- **时钟偏快（now 偏大）**：空闲期被高估，可能提前删除。对策是
  `grace_seconds` 宽限：年龄必须超过 `retention + grace` 才删，
  宽限应大于部署环境的最大预期时钟偏差。
- **未来时间戳**：记录时间戳“来自未来”超过容忍度时判为异常保留，
  而不是按字面时间参与计算。

### 暂停保留

`hold=True` 的记录无条件保留（优先级高于到期判定），用于合规留存、
审计冻结等场景；解除 `hold` 后下一轮清理即可正常到期删除。

## 引用完整性校验（清理前）

- **悬空引用**：`referenced_by` 指向不存在的记录 → 校验失败。
- **重复 ID**：同一 `record_id` 出现多次 → 校验失败。
- 校验失败默认**中止整批清理**（`cleanup_aborted=True`），不删任何记录。
- **删除后不变量**：若某幸存记录的唯一引用者恰在本批被删（隐式级联），
  后置校验同样中止整批清理，绝不静默级联（见
  `test_orphan_after_delete_aborts_instead_of_implicit_cascade`）。

## 可追溯性

每次评估产出 `CleanupReport`，`report.manifest()` 可 JSON 序列化，
包含：清理时刻、策略参数、每条记录的判定（delete/referenced/held/
active/anomaly）、理由码、引用者列表、异常明细、空闲时长。
`apply_cleanup()` 只接受 `apply=True` 且未被中止的报告，否则拒绝删除。

## 运行命令

```bash
cd retention_cleanup
python3 -m unittest test_retention -v   # 26 个自测
python3 demo.py                          # 三场景演示 + 写 outputs/manifest_*.json
python3 benchmark.py                     # 100 万条基准（耗时 + 峰值 RSS）
python3 benchmark.py --tracemalloc       # 额外测 Python 分配峰值
```

## 自测覆盖

- 全部到期（含边界 idle == retention、最近访问刷新空闲期、宽限期）
- 部分被引用（含自引用、互引环、共享父记录不级联）
- 暂停标记（持有期间保留、解除后可删）
- 时间异常（未来/负值/NaN/访问早于创建/容忍内轻微超前）
- 时钟不准（准确时钟 vs 回拨 30 天两类结果）
- 引用完整性（悬空引用、重复 ID 均中止清理）
- 可追溯性（清单理由与物理删除一致、未 apply 的报告拒绝删除）
- 引擎不读系统时钟（源码扫描守护）

## 100 万条记录基准结果

环境：Python 3.12.3，16 核，15GB 内存。数据集：70% 空闲到期、15% 活跃、
5% 暂停、3% 未来时间戳异常、7% 老但近期被访问；12 万条携带引用。

| 指标 | 数值 |
| --- | --- |
| 数据集构造 | 2.0 s |
| 清理评估（含完整性校验 + 全量判定清单） | **2.7 s** |
| 进程峰值内存（ru_maxrss） | **943 MB** |
| Python 分配峰值（tracemalloc，评估阶段） | 351 MB |
| 判定分布 | 删除 648,142 / 被引用 120,000 / 暂停 49,658 / 活跃 152,015 / 异常 30,185 |

原始输出见 `outputs/benchmark_1m.txt`、`outputs/benchmark_1m_tracemalloc.txt`。
内存大头是 100 万个 Record/Decision 对象本身；引擎采用 `__slots__`/
冻结 dataclass、理由码常量复用，评估过程为 O(n + 引用边数)。
