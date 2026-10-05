# 服务自愈与重启策略

纯 Python 3 标准库实现（`restart_policy.py`），所有时间由调用方注入
（`clock` 可调用对象或 `on_*(now=...)` 显式传参），决策完全确定、可回放。

## 1. 重启触发条件

以下任一事件发生，才会触发重启评估（不看"进程是否存在"）：

1. **进程退出**：`on_exited(now, exit_code)`。
2. **健康检查连续失败**：`on_health_check(ok=False)` 连续达到
   `health_check_max_failures`（默认 2）次 —— 进程还在但服务已不可用，
   同样判定该轮运行失败并重启。
3. 依赖恢复后，处于 `WAIT_DEPENDENCY` 的管理器重新安排重启。

不会触发重启的情况：重复/游离的退出事件、依赖已知不可用时的崩溃
（转为依赖探测）、已进入终止态 `FAILED` 之后的任何事件。

## 2. 退避方式与上限

指数退避，第 `n` 次连续失败后的等待：

```
delay(n) = min(base_delay * backoff_factor^(n-1), max_delay),  n >= 1
```

默认 `base=1s, factor=2, cap=60s`。重启时刻 = 判定时刻 + `delay`。

一次**存活成功**（见第 3 节）会把连续失败计数清零，退避回到 `base`。

**重启时刻序列**（默认测试配置：崩溃于 t=0，每次起来立刻崩，
base=1、factor=2、cap=16、最多 5 次连续失败）：

| 事件 | 时刻 |
|---|---|
| 首次启动 | 0 |
| 第 1 次重启 | 1   （退避 1s） |
| 第 2 次重启 | 3   （退避 2s） |
| 第 3 次重启 | 7   （退避 4s） |
| 第 4 次重启 | 15  （退避 8s） |
| 第 5 次重启 | 31  （退避 16s，命中上限） |
| 第 6 次崩溃 → 判定重启无效，终止 | 31 |

重启时刻序列即 **t = 1, 3, 7, 15, 31**（实际时间从故障发生点起算）。

## 3. 存活判定（双重依据）

一轮运行只有同时满足才算"存活成功"，只看进程起来不算：

1. **最小存活时长**：存活时间 ≥ `min_alive_seconds`（默认 10s）；
   起来很快又崩（quick crash）记为失败轮。
2. **健康检查结果**：连续失败次数未达到阈值（默认 2 次）。
   进程不退出但健康检查连续失败，也记为失败轮。

存活成功后：连续失败计数清零、退避重置、依赖不可用计时清零。

## 4. 无效重启识别与终止条件

连续失败轮计数 > `max_consecutive_failures`（默认 5，即第 6 次失败时）
判定**重启无效**，状态转为 `FAILED`，动作 `Action.STOP`，之后不再安排
任何重启，`stop_reason` 记录判定原因，例如：

> `restart ineffective: 6 consecutive failed runs (limit 5)`

这避免了"起来就崩 → 立刻重启 → 再崩"的无界重启循环掩盖故障。
终止后应由上层告警/人工介入；策略参数可按服务特点调整。

## 5. 依赖长期不可用时的行为

依赖状态由 `on_dependency_check(ok, now)` 上报：

- 已知依赖不可用时服务崩溃：**不重启服务**，返回 `PROBE_DEPENDENCY`，
  仅以固定慢节奏（默认 30s）探测依赖；服务重启次数为 0。
- 依赖在 `dependency_max_wait_seconds`（默认 600s）内恢复：按当前退避
  重新安排一次重启，之后照常评估。
- 依赖持续不可用超过 600s：判定为长期不可用，返回 `STOP` 并进入
  `FAILED`，终止重启，避免对下游造成持续冲击、也避免空转掩盖问题。

## 6. 复杂度与 10 万故障事件数据

管理器只保存固定数量的标量（计数器、时间戳），**状态 O(1)**，
单事件调度 **O(1)**，与事件总数无关。

在本机实测（Python 3，混合负载：每 3 次一次快速崩溃、其余为健康长运行）：

- 100,000 次故障事件调度：**约 0.16–0.20 s**（约 1.6–2.0 µs/事件）
- 状态内存占用：**约 0.75 KB，且事件处理前后不增长**
  （仅有 int/float 因数值位数产生的几字节差异，无历史列表堆积）

复现：`python3 demo.py`、`python3 -m unittest test_restart_policy -v`。

## 7. 文件与运行命令

- `restart_policy.py` —— 策略库（`Policy`、`RestartManager`、`Action`、`Decision`）
- `test_restart_policy.py` —— 9 个 unittest 自测
- `demo.py` —— 重启时刻序列、无效判定、依赖不可用、10 万事件基准

```bash
cd selfheal
python3 -m unittest discover -v   # 自测
python3 demo.py                   # 演示与基准数据
```

## 8. 驱动接入方式（真实环境）

`Decision(action, at, reason)` 是驱动层与策略层的唯一接口：

- `RESTART`：在 `at` 时刻启动服务，之后回调 `on_started`；
- `PROBE_DEPENDENCY`：在 `at` 时刻做一次依赖探测，回调 `on_dependency_check`；
- `STOP`：终止重启并告警；
- `NONE`：继续运行（期间按健康检查节奏回调 `on_health_check`）。
