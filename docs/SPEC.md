# 资源清单差异比对口径说明

## 1. 比对对象与匹配方式

- 清单格式：JSON。可以是资源对象数组，也可以是含 `resources` 数组的对象。
- 资源按身份字段 `id` 匹配（去首尾空白，大小写敏感）。
- **资源在清单中的排列顺序不参与比对**；同一资源在两边位置不同不算差异，
  仅记录 `resource_order` 信息项，判定合规。
- JSON 对象的字段顺序由解析器归一化，天然不参与比对。

## 2. 字段分级与判定规则

### 2.1 关键字段（KEY_FIELDS）——规范化后必须一致，不一致即违规

| 字段 | 含义 | 规范化规则（等价写法） | 必须一致的理由 |
|---|---|---|---|
| `id` | 资源身份 | 去首尾空白 | 匹配主键，缺失则无法逐条比对 |
| `type` | 资源类型 | 去空白、转小写 | 类型变更意味着资源性质改变 |
| `region` | 部署地域 | 去空白、转小写 | 地域涉及数据主权/合规边界 |
| `cpu` | CPU 核数 | 数值化（`4`/`"4"`/`4.0`/`"4.0"` 等价） | 容量/配额核心指标 |
| `memory_gb` | 内存 | 统一折算 GB（`16`/`"16GB"`/`"16384MB"` 等价） | 容量/配额核心指标 |
| `image` | 镜像 | 去空白、转小写 | 镜像决定软件供应链来源 |
| `encrypted` | 是否加密 | 布尔化（`true`/`"TRUE"`/`1`/`"yes"` 等价） | 数据安全的硬性合规项 |
| `ports` | 端口集合 | 解析为去重升序整数集合（`"80,443"`/`[443,80]` 等价） | 暴露面是安全审计核心 |
| `tags` | 标签 | 归一为键值字典（`["k=v"]` 与 `{"k":"v"}` 等价） | 标签承载归属/环境等治理信息 |

依据：这些字段直接决定资源的**安全属性、容量配额与合规边界**，任何
实质变化都必须拦截，因此判为**违规差异**。

### 2.2 允许不同字段（ALLOWED_FIELDS）——差异仅记录，判合规

`description`、`owner`、`updated_at`、`remarks`

依据：这些是**描述性/流程性元数据**，不改变资源的安全与容量属性，
差异属于正常运维演进，判为**合规差异**（仍留痕可审计）。

### 2.3 未知字段（口径之外）——待确认

凡不在上述两组之内的字段（如 `cost_center`），一律记 `unknown_field`，
判**待确认**：口径未覆盖的字段不能默认安全，需人工确认其合规要求后
再决定纳入哪一组。

## 3. 差异归因（kind）与合规判定（verdict）

三级判定：`compliant`（合规差异）、`violation`（违规差异）、
`needs_review`（待确认）。总体判定取最严一级：有违规即违规，
否则有待确认即待确认，否则合规。

| kind | 归因 | 判定 |
|---|---|---|
| `key_field_mismatch` | 关键字段规范化后不一致 | 违规 |
| `key_field_unparsable` | 关键字段取值无法按口径解析 | 违规（无法证明合规，按最严处理） |
| `resource_missing` | 基线有、候选无（资源被删/漏报） | 违规 |
| `resource_added` | 候选有、基线无（新增资源） | 待确认（需确认是否经审批） |
| `allowed_field_mismatch` | 允许不同字段变化 | 合规 |
| `unknown_field` | 口径外字段 | 待确认 |
| `missing_key_field` | 关键字段缺失/为空 | 待确认，**单独报出**（见 §4） |
| `duplicate_id` | 同一清单内 id 重复 | 待确认，单独报出 |
| `equivalent_notation` | 仅等价写法/集合顺序差异 | 合规，**不计入差异** |
| `resource_order` | 仅资源排列顺序不同 | 合规，不计入差异 |

## 4. 关键字段缺失单独报出

关键字段缺失或为空时，该字段**无法判定一致性**，因此：

- 记入报告顶层 `key_field_missing` 数组（含 side/resource_id/field），
  **不混入 `diffs`**，避免与真正的取值差异混淆；
- 该字段不再产生取值差异条目（不重复报告）；
- 判定为待确认（缺失不等于违规，但必须人工补齐后才能下结论）。

## 5. 等价写法与顺序不算差异

以下情形规范化后完全一致，只记入 `equivalent_notations` 信息数组
（判定合规），不计入 `diffs`：

- 数值写法：`4` vs `"4"` vs `4.0`
- 单位写法：`16` vs `"16GB"` vs `"16384MB"`
- 布尔写法：`true` vs `"TRUE"` vs `1`
- 大小写：`"Ubuntu-22.04"` vs `"ubuntu-22.04"`
- 集合顺序与写法：`[80,443]` vs `"443,80"`
- 标签写法：`{"env":"prod"}` vs `["env=prod"]`
- 资源排列顺序、JSON 字段顺序

## 6. 报告结构

```json
{
  "summary": {
    "overall_verdict": "violation|needs_review|compliant",
    "resources_baseline": 0, "resources_candidate": 0,
    "matched_resources": 0,
    "diff_counts": {"violation": 0, "compliant": 0, "needs_review": 0},
    "key_field_missing_count": 0,
    "equivalent_notation_count": 0
  },
  "key_field_missing": [ ... ],
  "diffs": [ {"kind","verdict","reason","resource_id","field","baseline","candidate"} ],
  "equivalent_notations": [ ... ]
}
```

## 7. 运行命令

```bash
# 自测（17 个用例，覆盖完全一致/仅顺序不同/等价写法/关键字段缺失等）
python3 -m unittest test_manifest_diff -v

# 比对两份清单并输出报告（违规时退出码为 1，可用于 CI 卡点）
python3 run_diff.py samples/baseline.json samples/candidate.json -o output/report.json

# 1 万份清单基准（耗时 + 内存峰值）
python3 benchmark.py --count 10000 --resources 20
```
