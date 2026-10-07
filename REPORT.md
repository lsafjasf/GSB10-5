# 影响面分析报告与基准数据

环境: Python 3.12.3, Linux x86_64。以下数据由 `python3 demo.py` 与
`python3 benchmark.py` 实际运行产出。

## 一、影响清单样例（分层 + 推导路径）

场景: payment/search 两个业务域各有 `db.timeout`（同名不同义）；
`payment.db.url` 模板引用 `payment.db.host`；`gateway → cache-client`
模块链；`cache.{region}.ttl` 动态拼接（变量域已知）；`feature.{flag}.enabled`
动态拼接（变量域未知）。

### 变更 payment.db.timeout —— 直接引用 + 同名不同义判定

```
[直接引用] 1 项
  - pay-service  (推导路径: payment.db.timeout -> pay-service)
[间接受影响] 0 项
[动态引用待人工确认] 0 项 (未解析动态引用库存总数: 1)
```

判定结果: `search-service` 引用的是 `search.db.timeout`，完全限定名不同，
**未出现在清单中**——同名不同义未被误判。反向变更 `search.db.timeout`
时 `pay-service` 同样不受影响（自测双向验证）。

### 变更 payment.db.host —— 间接引用（配置模板链）

```
[直接引用] 1 项
  - payment.db.url  (推导路径: payment.db.host -> payment.db.url)
[间接受影响] 1 项
  - pay-service  (深度2, 推导路径: payment.db.host -> payment.db.url -> pay-service)
```

### 变更 cache.cn.ttl —— 动态拼接（已解析）+ 模块依赖链

```
[直接引用] 1 项
  - cache-client  (推导路径: cache.cn.ttl -> cache-client)
[间接受影响] 1 项
  - gateway  (深度2, 推导路径: cache.cn.ttl -> cache-client -> gateway)
```

`cache.{region}.ttl` 变量域 region∈{cn,us} 已知，展开后与已登记配置求交，
命中 `cache.cn.ttl` / `cache.us.ttl` 各建一条边。

### 变更 feature.new_ui.enabled —— 动态拼接（未解析，待人工确认）

```
[直接引用] 0 项
[间接受影响] 0 项
[动态引用待人工确认] 1 项 (未解析动态引用库存总数: 1)
  - recommender 的模式 feature.{flag}.enabled 可能命中变更项 feature.new_ui.enabled
```

变量域未知无法展开，但结构匹配（段数相同、字面量段相等）上变更项，
单独列入待人工确认；不匹配的变更（如 `payment.db.host`）该清单为空，
而未解析模式库存总数始终随报告上报，不会被遗漏在清单外。

## 二、一致率数据（10 万配置 / 100 万引用）

基准答案由独立实现的暴力 BFS（直接遍历原始引用记录）给出，与本库图分析
结果逐项比对，抽样 200 个变更项：

| 指标 | 数值 |
| --- | --- |
| 结果完全一致样本 | 200 / 200 (100.0%) |
| 影响集合 精确率 / 召回率 | 100.00% / 100.00% |
| 待人工确认清单 精确率 / 召回率 | 100.00% / 100.00% |

## 三、耗时与内存峰值（10 万配置 / 100 万引用）

引用构成: 静态 875,000 + 依赖链 40,000 + 配置模板 50,000 +
动态已展开 30,000 + 动态未解析 5,000 = 1,000,000 条。
图规模: 125,000 节点 / 995,000 边 / 5,000 条未解析动态引用。

| 指标 | 数值 |
| --- | --- |
| 建图（含动态展开/求交） | 2.763 s |
| 单次变更影响分析 | 平均 28.1 ms，P95 47.7 ms（200 样本） |
| tracemalloc Python 对象峰值 | 60.9 MB |
| 进程 RSS 峰值 (ru_maxrss) | 222.7 MB |
| 平均/最大受影响节点数 | 29.7 / 254 |

## 四、自测覆盖

`python3 selftest.py -v`：7 例全部通过——直接引用、间接引用（配置模板链、
模块依赖链）、同名不同义（双向）、动态拼接已解析、动态拼接未解析
（命中入待确认清单、未命中不入但库存计数仍在）。
