# 证书吊销状态缓存 — 策略说明

## 1. 生存期与刷新策略

- 条目字段：`status`、`fetched_at`、`expires_at = fetched_at + ttl`、`revoked_sticky`。
- 缓存策略为 **lazy TTL（读时刷新，无后台线程）**：`check()` 命中且未过期直接返回，过期则同步向上游（OCSP/CRL 适配层）查询一次并覆盖条目。
- 过期判定（半开区间，边界确定）：`now < expires_at` 为 fresh；`now == expires_at` 即过期，立即刷新。
- 默认 `ttl = 900s`（15 分钟），`stale_window = 300s`。
- 另提供 `invalidate(serial)`，供 CRL/OCSP 主动推送侧在收到吊销通知时立即失效条目。
- 不缓存上游的 `UNKNOWN`（无数据）：`UNKNOWN` 走"查询不可用"策略，不污染缓存。

## 2. 先取舍：查询不可用时放行还是拦截？

**默认拒绝（fail-closed）。** 吊销检查是安全控制，放行等于在依赖方不可见的情况下关闭这道控制；
拒绝会把故障显性化。可用性优先的场景可显式选择 `ALLOW`（故障期间放行，但结论标记
`status=UNKNOWN / source=no-data`，调用方不得将其视为"证书有效"），或 `DEGRADED`
（过期后 `stale_window` 内使用旧值，窗口外拒绝）。

### 2.1 正常判定表（查询成功 / 缓存可用）

| 缓存/上游状态 | 来源 | 判定 | status |
|---|---|---|---|
| GOOD | live | 放行 ALLOW | GOOD |
| GOOD | cache（TTL 内） | 放行 ALLOW | GOOD |
| GOOD | stale（降级窗口内） | 放行 ALLOW | GOOD |
| REVOKED | live/cache/stale | 拒绝 REJECT | REVOKED |
| UNKNOWN（上游返回） | — | 按 2.2"无可用值"处理 | UNKNOWN |

### 2.2 查询不可用（超时 / 报错 / UNKNOWN）判定表

| 策略 | 无缓存可用 | 有 GOOD 旧值，过期 ≤ stale_window | 有 GOOD 旧值，超出 stale_window | 曾判 REVOKED（粘性） |
|---|---|---|---|---|
| DENY（默认） | REJECT (no-data) | REJECT (no-data) | REJECT (no-data) | REJECT (stale, REVOKED) |
| ALLOW | ALLOW (no-data) | ALLOW (no-data) | ALLOW (no-data) | REJECT (stale, REVOKED) |
| DEGRADED | REJECT (no-data) | ALLOW (stale) | REJECT (no-data) | REJECT (stale, REVOKED) |

- 粘性吊销对三种策略一视同仁：`REVOKED` 永不放行（见第 3 节）。
- `ALLOW` 放行的是"无数据"，`Verdict` 中 `status=UNKNOWN`、`source=no-data`，
  与确认 GOOD 可区分，便于审计。

## 3. 吊销粘性（Revoked 不得翻回有效）

- 一旦对某序列号观察到 `REVOKED`，条目标记 `revoked_sticky=True`。
- 之后无论刷新返回 GOOD、UNKNOWN 还是查询抛错，判定恒为 REJECT；
  即使选择 `ALLOW` 策略也不得放行（安全底线优先于可用性策略）。
- 刷新成功但粘性条目时，仅更新 `fetched_at/expires_at`，状态保持 REVOKED。
- 粘性仅限本缓存实例生命周期；确需重置（例如误判纠正），应新建缓存实例或重启进程，
  避免静默放开已拒证书。
- 验证数据（`test_revoked_sticky_*`，注入时钟 + 脚本化 fetcher）：
  - REVOKED → 刷新得 GOOD：两次判定均 REJECT/REVOKED（fetcher 调用 2 次）。
  - REVOKED → 刷新超时：REJECT/REVOKED，source=stale。
  - REVOKED + ALLOW 策略 + 刷新超时：仍 REJECT/REVOKED。
  - GOOD → REVOKED → GOOD 连续刷新：后两次均 REJECT/REVOKED。

## 4. TTL 内状态变化（滞后）的处理

- **接受有界滞后**：TTL 内不查询，证书若在 TTL 内被吊销，最坏滞后时间 = TTL
  （默认 900s）。滞后期间该证书按旧值放行——这是缓存换性能的固有代价。
- 缩短 TTL 可减小滞后但增加上游负载；需要近实时的场景接入吊销推送并调用
  `invalidate()`，可将滞后压到推送延迟级别（自测 `test_invalidate_cuts_lag` 验证）。
- 反向变化（REVOKED → GOOD）不存在滞后放行问题：粘性保证持续拒绝。

## 5. 验证数据

`python3 -m unittest -v`：15 个用例全部通过，覆盖
缓存命中不回源、恰好过期边界（`now == expires_at`）、查询超时/报错/上游 UNKNOWN、
三种不可用策略、降级窗口内外、GOOD→REVOKED 状态变更、吊销粘性（含 GOOD 翻案、
超时、ALLOW 策略下仍拒绝）、TTL 内滞后与 invalidate 消除滞后。

## 6. 耗时与内存（100 万次 check，本机实测）

| 场景 | 耗时 | 吞吐 | 缓存内存峰值 | 回源次数 |
|---|---|---|---|---|
| 单证书（全程命中） | 1.510 s | 0.66 M 次/s | < 0.01 MiB | 1 |
| 10 万不同证书（稳态命中） | 2.479 s | 0.40 M 次/s | 12.83 MiB（≈135 B/条） | 100000 |

- 时间冻结（注入 clock 恒为 0）保证走纯命中路径；内存为 `tracemalloc` 峰值。
- 结果为单机数据（Python 3.12），复跑 `python3 bench.py`。

## 7. 运行命令

```bash
python3 -m unittest -v        # 自测
python3 bench.py              # 100 万次基准
```

仅使用 Python 标准库；时间通过 `clock` 注入，上游查询通过 `fetcher` 注入
（抛 `RevocationTimeoutError`/`RevocationSourceError` 或返回 `UNKNOWN` 表示不可用）。
