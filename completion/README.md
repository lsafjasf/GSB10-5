# 交互式命令行补全（Python 3，仅标准库）

文件：

| 文件 | 作用 |
|---|---|
| `completion_lib.py` | 补全核心库：`CompletionIndex`（索引 + 权限过滤 + 版本化缓存） |
| `test_completion.py` | 自测：候选极少 / 极多 / 敏感名称 / 权限刚变化，共 12 例 |
| `bench.py` | 基准：100 万候选、10 万次补全，输出 `BENCHMARK_RESULTS.md` |
| `demo_cli.py` | 交互式 CLI（readline TAB 补全，可在线授权/撤权） |
| `BENCHMARK_RESULTS.md` | 实测耗时分布、内存峰值、权限校验数据 |

## 运行命令

```bash
cd completion
python3 test_completion.py        # 自测
python3 bench.py                  # 基准（约 5 秒），重新生成 BENCHMARK_RESULTS.md
python3 demo_cli.py --size 200000 # 交互演示：输入前缀回车，或 :grant dev secret 后再补全
```

## 索引方式

候选名按**权限组**分桶，每组维护一条**有序数组**：

```
group "public" -> ["admin_0000001", "apple_0000002", ...]   # 已排序
group "secret" -> ["secret_0000000", ...]
user  "dev"    -> {public, ops}        # 用户 -> 组集合
```

一次补全 `complete(user, prefix, limit)`：

1. 取用户的组集合，**只打开这些组的有序数组**（无权组的数组根本不被触碰，
   因此无权名字不可能进入归并 —— 权限过滤在返回之前、且在索引层完成）；
2. 对每条数组 `bisect_left(prefix)` 定位起点（O(log m)），
   向后扫描 `startswith(prefix)`，**每组最多取 limit 条即停**；
3. `heapq.merge` 归并各组有序流，去重，凑满 `limit` 即返回。

**大候选量下为何仍然快**：单次查询代价是
`O(Σ log m_g + limit·log G)`，与总候选数 N 无关；
宽前缀（如前 1~3 字符，匹配几十万条）也被 `limit` 立即截断，
实测宽前缀 p99 < 1µs。结果缓存按 `(user, prefix, limit)` 记忆，
键内带权限版本号，权限一变即整体失效。

内存：名字字符串只存一份，组数组里存引用（每条 8 字节）。
100 万候选 ≈ 字符串 62 MB + 数组槽 8.8 MB + 字典开销 31 MB ≈ **102 MB**
（进程 RSS 峰值约 560 MB，含解释器与基准数据生成阶段的临时对象）。

## 权限在补全过程中变化：确定性语义

- 每次权限变更（`set_user_groups` / `add` / `remove`）使全局版本号 +1，
  并清空结果缓存 —— 下一次补全必然看到新权限，**不会读到过期缓存**；
- 单次 `complete()` 在调用开始处对组数组做快照，返回的结果
  **恰好对应一个权限版本**，不会混杂两个版本的名字；
- 流式接口 `complete_iter()` 在迭代途中发现版本变化时抛出
  `PermissionChanged`，调用方重新查询即可 —— 宁可明确失败，
  也不返回新旧权限混合的结果（见自测
  `test_streaming_detects_mid_iteration_change`）。

## 部署提示

大批量加载用 `bulk_add`（O(N log N)，100 万条约 1.6 s）；
加载完成后调用 `gc.collect(); gc.freeze()`，把百万级长命对象移出
循环 GC 扫描范围 —— 实测 p99.9 从 ~137µs 降到 ~42µs，
最坏延迟从 ~39ms 降到 ~2ms（见 `bench.py` 注释与实测数据）。

## 实测数据摘要（完整数据见 BENCHMARK_RESULTS.md）

环境：Python 3.12.3 / Linux，100 万候选（含 2 万敏感名），10 万次补全，limit=50。

- 延迟（无权限用户 dev，冷缓存）：p50=0.4µs p90=7.9µs p95=12.4µs
  p99=15.2µs p99.9=39.3µs max=1.75ms
- 泄漏校验：10 万次查询共返回 4,234,013 个名字（含 1 万次直接瞄准
  敏感名前缀的探测），与 2 万敏感名集合逐一比对，**泄漏 0**
- 权限变更：撤权前 admin 得 50,000 条敏感结果 → 撤权后 0 条
  （1000/1000 查询为空）→ 重新授权后恢复 50,000 条
- 内存峰值：进程 RSS 峰值 559.5 MB；索引本体约 102 MB
