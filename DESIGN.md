# 动态日志级别调整 — 设计与传播说明

针对“排障时级别一调到底、日志量瞬间涨几百倍、磁盘先被写满”的问题，
本库提供：级别动态下发 + 日志量上限联动自动收敛 + 全链路审计 + 一致性校验。
纯 Python 3 标准库，无第三方依赖。

## 模块

- `dynlog/levels.py` — 级别定义（DEBUG/INFO/WARN/ERROR/OFF）与升降级工具
- `dynlog/manager.py` — 级别下发、传播模拟、一致性校验
- `dynlog/guard.py` — 日志量上限守卫（超限自动收敛、回落自动释放）
- `dynlog/audit.py` — append-only 审计记录
- `dynlog/simulate.py` — 日志量仿真（按已生效级别产生日志）
- `tests/test_dynlog.py` — 8 个自测
- `demo.py` — 端到端场景（1000 源），产出 out/ 下全部数据
- `bench.py` — 1000 源 × 10 万次调整压测

## 传播方式与生效时机

采用**控制面推送（push）**模型，与真实配置中心下发同构：

1. `set_level(source, level, operator)` 只改控制面的“期望状态”，生成一条
   投递事件，`投递时刻 = 当前时间 + delay_fn(source)`（每节点传播延迟，
   demo 中用 crc32 模拟 1~50ms 网络抖动）。
2. 事件到达节点才生效（`advance_to(t)` 应用到期事件）——即“下发”与
   “生效”分离，生效时刻可精确记录（`SourceState.applied_at`）。
3. 每节点维护单调递增 `change_seq`；过期/乱序事件直接丢弃，
   **last-write-wins**，反复调整不会回退到中间态。
4. 守卫的自动调整走**同一条传播通道**（`set_guard_level`），
   生效语义与人工调整完全一致。

各节点生效时间数据见 `out/propagation.csv`（两次变更 × 1000 节点，
逐节点 requested_at / applied_at / latency_ms）。汇总：

```
INFO->DEBUG: nodes=1000 min=1.0ms p50=26.0ms p95=49.0ms p99=50.0ms max=50.0ms
DEBUG->INFO: nodes=1000 min=1.0ms p50=26.0ms p95=49.0ms p99=50.0ms max=50.0ms
```

## 与日志量上限联动（自动收敛）

`VolumeGuard.observe(volumes)` 每个观测周期执行：

- **超限**：总日志量 > cap 时，按日志量从大到小对最吵的源逐级调高
  guard 覆盖级别（DEBUG→INFO→WARN→ERROR），直到估算削减量
  （reduction_factor=3）覆盖超出部分；高频源最先被收敛。
- **回落释放**：总量 ≤ cap×0.9 且持续 3 个周期，逐级往回放；
  覆盖级别 ≤ 操作者期望级别时判定冗余，立即解除。
- 有效级别 = max（操作者期望， guard 覆盖），守卫只会比人工更保守。

日志量曲线见 `out/volume_curve.txt`（ASCII 图）与
`out/volume_curve.csv`（逐周期数据）。关键片段：

```
t=2.0s  8940   (INFO 基线，cap=9000)
t=2.1s  29800  (全量调 DEBUG，3.3x 超限，守卫当周期介入)
t=2.2s  8940   (1000 个源被收敛到 guard=INFO，回到上限内)
t=12.0s 8940   (人工调回 INFO，守卫覆盖自动释放，guarded 1000→0)
```

## 一致性校验

`consistency_check()` 逐节点核对：已生效级别 == 期望级别，且
`applied_seq == change_seq`（无滞留的未应用变更）。`out/consistency.txt`：

```
check@t=12.0s total=1000 ok=False mismatches=1000   <- 调回瞬间，变更在途，被如实识别
check@t=12.1s total=1000 ok=True  mismatches=0      <- 最大传播延迟 50ms 后全部生效
check@t=18.0s total=1000 ok=True  mismatches=0      <- 终态
guard overrides remaining: 0                        <- 守卫覆盖无残留
requested==effective on all 1000 sources: True
```

## 审计

每次调整（人工 + auto-guard）写一条 append-only 记录：序号、时间戳、
操作者、日志源、旧级别→新级别、版本号、原因。全量见 `out/audit.log`
（demo 共 4000 条），示例：

```
#1 2026-10-06T12:01:27.408 operator=oncall-zhang source=src-0000 INFO->DEBUG version=1 reason=INC-1024 checkout 5xx spike
#3999 2026-10-06T12:01:27.433 operator=auto-guard source=src-0998 INFO->- version=1999 reason=override redundant, release
```

## 性能（1000 源 × 10 万次调整，bench.py）

```
wall_time≈0.86~1.06s  throughput≈9.5万~11.6万 ops/s  avg≈8.6~10.6us/op
tracemalloc_peak=28.9MB  ru_maxrss=106.6MB
audit_records=100000  final consistency: total=1000 ok=True mismatches=0
```

（含 10 万条审计记录驻留内存；Python 3.12，无第三方依赖。）

## 运行命令

```bash
python3 -m unittest discover -s tests -t . -v   # 自测（8 个用例）
python3 demo.py                                  # 场景演示，产出 out/ 全部数据
python3 bench.py                                 # 1000 源 × 10 万次调整压测
```

## 自测覆盖

- 单次调整：生效时机（投递前不生效、投递后生效）、版本号、审计字段
- 反复来回调整：20 轮 × 50 源 flip-flop，全部收敛到最终期望，无滞留
- 乱序投递：旧事件晚到被丢弃，last-write-wins
- 高频日志源：最吵的源被守卫优先收敛，总量回落到 cap 内
- 调整后日志量超限：全量 DEBUG → 超限 → 自动收敛 → 调回 → 守卫释放
  → 一致性 0 失配
- 一致性校验：能识别在途未生效节点
