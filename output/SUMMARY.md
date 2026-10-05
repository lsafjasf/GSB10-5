# 资源清单比对结果摘要（样本数据）

- 总体判定：**违规**
- 基线资源数：4；候选资源数：4；匹配：3
- 差异数：违规 2、合规 2、待确认 2
- 关键字段缺失：1（单独报出）
- 等价写法/顺序信息项：8（不计为差异）

## 差异归因明细

| 资源 | 归因 | 字段 | 判定 | 基线 | 候选 |
|---|---|---|---|---|---|
| vm-legacy | 资源缺失 | - | 违规 | {"id": "vm-legacy", "type": "compute.vm", "region": "cn-north-1", "cpu": 2, "memory_gb": 4, "image": "centos-7", "encrypted": false, "ports": [22], "tags": {"env": "staging"}, "description": "待下线", "owner": "ops@example.com"} | null |
| vm-new | 资源新增 | - | 待确认 | null | {"id": "vm-new", "type": "compute.vm", "region": "cn-east-1", "cpu": 4, "memory_gb": 16, "image": "ubuntu-22.04", "encrypted": true, "ports": [443], "tags": {"env": "prod"}, "description": "新申请资源", "owner": "dev@example.com"} |
| db-001 | 关键字段不一致 | memory_gb | 违规 | 32 | 64 |
| db-001 | 口径外未知字段 | cost_center | 待确认 | null | "CC-100" |
| vm-001 | 允许不同字段变化 | description | 合规 | "订单服务主机" | "订单服务主机（已扩容说明更新）" |
| vm-001 | 允许不同字段变化 | owner | 合规 | "ops@example.com" | "ops-lead@example.com" |

## 关键字段缺失（单独报告）

- side=candidate resource=cache-001 field=region：关键字段 region 缺失或为空，无法判定一致性，需人工确认

## 等价写法 / 顺序（合规，不算差异）

- equivalent_notation type: "compute.vm" -> "Compute.VM"
- equivalent_notation region: "cn-east-1" -> "CN-EAST-1"
- equivalent_notation cpu: 4 -> "4"
- equivalent_notation memory_gb: 16 -> "16GB"
- equivalent_notation image: "ubuntu-22.04" -> "Ubuntu-22.04"
- equivalent_notation encrypted: true -> "TRUE"
- equivalent_notation ports: [80, 443] -> "443,80"
- equivalent_notation tags: {"env": "prod", "team": "core"} -> ["team=core", "env=prod"]

# 1 万份清单基准结果

- 清单对数：10000；每份 20 个资源；共比对资源 200000 个
- 总耗时：5.495 s（单份均值 0.549 ms，1819.9 对/秒）
- 开启 tracemalloc 时耗时：26.768 s（仅用于测内存，有插桩开销）
- Python 对象内存峰值（tracemalloc）：0.08 MiB（流式逐对比对）
- 进程 RSS 峰值（getrusage）：22.49 MiB
- 聚合差异：{"violation": 8886, "compliant": 5880, "needs_review": 8878}；关键字段缺失 5862；等价写法 122103
