# 证书吊销状态缓存 — 策略说明

## 0. 核心取舍（先讲清楚）

**查询不可用时的默认策略：拒绝（fail-closed）。** 吊销检查是安全控制，
"查不动就放行"等于给攻击者一个开关：阻断 OCSP/CRL 通道即可让已吊销证书通过。
因此库默认 `FAIL_CLOSED`；`FAIL_OPEN` 与 `FAIL_DEGRADED` 作为显式可选项存在，
供可用性优先的场景自行选择，但无论哪种模式，**已判吊销的证书一律拒绝**（钉住规则优先于一切失败策略）。

## 1. 生存期与刷新策略

- **TTL**：默认 `ttl=300s`（可配置）。条目在拉取成功时计时。
- **过期计算**：`expires_at = min(fetched_at + ttl, next_update)`。
  `next_update` 是上游（CRL 的 nextUpdate / OCSP 的 nextUpdate）给出的状态有效期截止，
  若上游未提供则只用 `ttl`。判定规则：`now < expires_at` 为有效；
  **`now == expires_at` 即视为过期**（严格小于，边界不外延）。
- **刷新策略**：惰性刷新（lazy）——缓存命中且未过期时不访问上游；
  未命中或过期时才同步查询上游并覆盖缓存。不做后台主动刷新，
  避免为冷门证书浪费上游配额；热点证书由流量自然驱动刷新。
- **内存回收**：提供 `evict_expired()` 清理过期条目（吊销钉住记录不清除），
  由调用方按需定期调用；不调用时内存随不同证书数线性增长（约 142 字节/条）。

## 2. 查询不可用（超时 / 报错）时的判定表

设：缓存条目状态 ∈ {无缓存, 有效（未过期）, 已过期且在 max_stale 窗口内, 已过期且超出窗口}；
"已钉住" = 该证书曾被判定 REVOKED。

| 情形 | FAIL_CLOSED（默认） | FAIL_OPEN | FAIL_DEGRADED |
|---|---|---|---|
| 已钉住吊销 | **拒绝** | **拒绝** | **拒绝** |
| 缓存有效 | 用缓存结果 | 用缓存结果 | 用缓存结果 |
| 无缓存，查询失败 | 拒绝 | 放行 | 拒绝 |
| 有缓存但已过期，≤ max_stale | 拒绝 | 放行 | 降级：用旧缓存结果 |
| 有缓存但已过期，> max_stale | 拒绝 | 放行 | 拒绝 |

要点：
- 钉住规则是第一行，优先于所有失败策略——fail-open 也不能放行已吊销证书。
- FAIL_DEGRADED 的降级窗口 `max_stale` 默认 3600s，是可接受的风险敞口上限，需显式权衡。

## 3. 吊销状态的单调性（不得回翻）

- 任一时刻上游返回 REVOKED，该 serial 立即进入**钉住表**（`_revoked_pins`）。
- 钉住后 `check()` 短路返回拒绝，**不再访问上游**，因此任何后续刷新
  （包括上游故障、上游被篡改后返回 GOOD）都不可能把它变回有效。
- 钉住记录不参与 `evict_expired()` 清理。

**验证数据**（`test_revoked_never_flips_back`）：

| 步骤 | 上游状态 | 时刻 | check 结果 | 说明 |
|---|---|---|---|---|
| 1 | REVOKED | t=1000 | 拒绝（fresh） | 拉取到吊销，写入钉住表 |
| 2 | 上游改口 GOOD | t=11000（缓存早过期） | 拒绝（pinned） | 上游查询次数未增加，结果未被覆盖 |

另有 `test_revoked_pin_overrides_fail_open`：FAIL_OPEN 模式下钉住的吊销证书
在上游超时时仍被拒绝。`test_revoked_persists_across_eviction`：清理过期缓存后钉住仍生效。

## 4. 缓存有效期内状态变更的滞后（明确接受）

证书在 TTL 内由 GOOD 变为 REVOKED 时，缓存仍按旧结果放行，直到条目过期。
**我们接受这段滞后**，其上界为 `min(ttl, next_update - fetched_at)`。
这是 CRL/OCSP 类拉取模型的固有特性（与 CRL nextUpdate 语义一致），
用 ttl 换上游负载与延迟。对吊销时效要求高的证书，应调小 ttl 或改用
OCSP stapling / 短有效期证书，而不是绕过缓存。

**验证数据**（`test_good_to_revoked_lag_within_ttl`，ttl=300）：

| 步骤 | 上游状态 | 时刻 | check 结果 | 来源 |
|---|---|---|---|---|
| 1 | GOOD | t=1000 | 放行 | fresh |
| 2 | 变为 REVOKED | t=1100（TTL 内） | 放行 | cache（滞后窗口内） |
| 3 | REVOKED | t=1301（已过期） | 拒绝 | fresh（刷新后拿到吊销） |

## 5. 性能数据（100 万次校验）

环境：Python 3.12.3，Linux x86-64（容器），单线程；
上游用内存 Fake 替代，测缓存自身开销；50,000 个不同证书，ttl=3600s。

| 指标 | 数值 |
|---|---|
| 总耗时（100 万次） | ≈ 3.3–3.7 s（三次运行 3.26 / 3.69 / 3.37 s） |
| 单次均耗 | ≈ 3.3–3.7 µs |
| 吞吐 | ≈ 28–31 万次/秒 |
| 上游拉取次数 | 50,000（缓存命中率 95%） |
| 缓存内存（tracemalloc） | 6.79 MiB，约 142 字节/条目 |
| 100 万校验期间内存峰值 | 6.79 MiB（稳态无增长，全部命中） |

内存随不同证书数线性增长；百万级不同证书约 140 MiB，可用 `evict_expired()` 回收。

## 6. 运行命令

```bash
cd revocation
python3 -m unittest test_revocation_cache -v   # 13 个自测
python3 benchmark.py                            # 100 万次基准
```

## 7. 自测覆盖清单

| 要求情形 | 测试 |
|---|---|
| 缓存命中 | `test_cache_hit_no_second_fetch` |
| 缓存刚过期（边界） | `test_expiry_boundary_is_expired`（t+299.9 命中，t+300.0 重新拉取） |
| next_update 缩短 TTL | `test_next_update_shortens_ttl` |
| 查询超时 × 三策略 | `TestFailurePolicies` 共 6 例 |
| 状态变更为吊销（TTL 内滞后） | `test_good_to_revoked_lag_within_ttl` |
| 吊销不得回翻 | `TestRevocationMonotonicity` 共 3 例 |
