# 导出格式协商规则

库：`export_negotiation`（Python 3，仅标准库）。入口：`negotiate(requested_format, fields)`。

## 协商顺序

每次协商严格按以下顺序执行，前一阶段得出结果即返回：

1. **规范化（R1）**：格式名小写、去首尾空白、别名解析（`yml→yaml`、`json5→json`）；
   字段按 `name` 去重（保留首个）并按名称排序 —— 结果与输入顺序、大小写无关。
2. **精确匹配（R2）**：请求格式已注册且能覆盖全部字段 → `exact`。
3. **降级（R3/R5/R6）**：请求格式会丢字段（或格式未注册）时，依次尝试：
   - 沿该格式的**降级链**（`fallbacks`，按声明顺序逐环展开）；
   - 链无果则**族内回退**：取同 `kind`（text/binary）中 `(-priority, name)` 最小的格式；
   - 第一个能**全覆盖**字段的候选胜出 → `downgraded`，
     结果附 `requested_missing_fields`（原格式会丢的字段）与选择依据。
4. **部分成功（R4）**：没有任何格式能全覆盖时，若请求格式已注册，
   仍按请求格式导出可导出字段 → `partial`，附 `missing_fields` 缺失清单
   （字段名、类型、缺失原因）与 `full_support_alternatives`（能全覆盖的其他格式，通常为空）。
5. **失败（R7）**：请求格式未注册且无可用降级 → `failed`，
   返回 `available_options`：全部可用选项（按 `-priority, name` 排序），
   每项含格式数据（kind/priority）、对当前字段的覆盖情况与选择理由。

## 规则依据

- **数据完整性优先于格式偏好**（R3）：用户要的是数据，格式是载体。
  请求格式会静默丢字段时，优先降级到无损格式；仅当无损不可得才部分导出。
- **降级链按数据形状设计**（R5）：结构化格式（xml/yaml）降 json，扁平表格（tsv/xlsx）降 csv，
  文档格式（pdf）降 html —— 尽量保住原格式的表达能力。
- **族内回退保类型**（R6）：二进制格式不降级为文本格式（避免嵌入问题），反之亦然。
- **失败必须给选项**（R7）：直接报错没有行动价值；返回带覆盖情况与理由的选项列表，
  调用方可以自动选择（取第一个 `covers_all_fields=true` 的选项）或展示给用户。
- **确定性（R8）**：所有排序使用显式键 `(-priority, name)`，不依赖 dict/哈希迭代序；
  字段先排序再比较；同一请求在任意进程、任意次数重复协商结果一致
  （见 `check_determinism.py`，不同 `PYTHONHASHSEED` 下结果 sha256 相同）。

## 结果结构

| 字段 | 说明 |
| --- | --- |
| `status` | `exact` / `downgraded` / `partial` / `failed` |
| `requested_format` | 规范化后的请求格式 |
| `selected_format` | 选中的导出格式（`failed` 时为 `null`） |
| `exported_fields` | 实际可导出的字段名 |
| `missing_fields` | 缺失清单 `[{name, type, reason}]` |
| `notes` | 人可读的协商说明 |
| `requested_missing_fields` | 仅 `downgraded`：原请求格式会丢的字段 |
| `full_support_alternatives` | 仅 `partial`：能全覆盖的其他格式 |
| `available_options` | 仅 `failed`：全部可用选项及选择理由 |

## 选项数据

见 `export_negotiation/registry.py`：8 种格式（json/csv/tsv/xlsx/xml/yaml/html/pdf），
每种声明 `kind`、`priority`、`field_types`、`fallbacks`、`aliases`、`reason`。
字段类型全集：string / number / boolean / date / nested / binary / null / richtext。

## 自测与基准

- 单元测试 + 对拍：`python -m export_negotiation.test_negotiator`
  （15 项：精确命中、别名、降级链、族内回退、部分成功、失败选项、确定性、
  500 条实时 oracle 对拍、2000 条 `golden_cases.jsonl` 对拍）
- 重新生成对拍数据：`python -m export_negotiation.generate_golden`
- 跨进程确定性：`PYTHONHASHSEED=0 python -m export_negotiation.check_determinism`
- 10 万次基准：`python -m export_negotiation.benchmark`
