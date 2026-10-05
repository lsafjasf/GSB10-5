# 环境参数差异对比报告

对比对象: `data/env_a.properties` vs `data/env_b.properties`

## 汇总

| 分类 | 数量 |
| --- | --- |
| 完全一致 | 2 |
| 写法不同但等价 | 3 |
| 有意差异 | 2 |
| 待确认 | 1 |
| 疑似遗漏 | 4 |

## 疑似遗漏（4 项）

- `api.token` — 无声明/白名单/基线记录的新增差异，疑似遗漏
  - data/env_a.properties: `cf9035e9aef1`（SHA-256 指纹）
  - data/env_b.properties: `0b48bd337fc5`（SHA-256 指纹）
- `log.retention` — 历史基线中两边一致，当前出现未声明差异，疑似遗漏
  - data/env_a.properties: `7`
  - data/env_b.properties: `30`
- `monitor.endpoint` — 参数在 env_b 中缺失，且不在白名单，疑似遗漏
  - data/env_a.properties: `http://prometheus:9090`
  - data/env_b.properties: `None`
- `rpc.timeout` — 历史基线中两边一致，当前出现未声明差异，疑似遗漏
  - data/env_a.properties: `3000`
  - data/env_b.properties: `6000`

## 待确认（1 项）

- `thread.pool` — 历史基线中已存在该差异，但无声明/白名单依据，待确认
  - data/env_a.properties: `8`
  - data/env_b.properties: `16`

## 有意差异（2 项）

- `cache.size` — 命中允许差异白名单: 缓存容量按机型配置，生产机器内存更大
  - data/env_a.properties: `4GB`
  - data/env_b.properties: `8GB`
- `region` — 符合声明的期望值: 预发在国内地域，生产在海外地域，按部署架构有意区分
  - data/env_a.properties: `cn`
  - data/env_b.properties: `us`

## 写法不同但等价（3 项）

- `app.enabled` — 值等价但写法不同（大小写/空格/单位/列表顺序），不计为差异
  - data/env_a.properties: `True`
  - data/env_b.properties: `true`
- `feature.flags` — 值等价但写法不同（大小写/空格/单位/列表顺序），不计为差异
  - data/env_a.properties: `audit,retry,circuit`
  - data/env_b.properties: `circuit, audit ,retry`
- `jvm.heap.size` — 值等价但写法不同（大小写/空格/单位/列表顺序），不计为差异
  - data/env_a.properties: `2GB`
  - data/env_b.properties: `2048mb`

## 完全一致（2 项，从略）
