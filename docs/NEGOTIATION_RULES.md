# 数据导出格式协商规则

## 1. 输入与输出

- 输入：`requested_format`（字符串）+ `fields`（`(字段名, 字段类型)` 有序列表）。
- 输出：纯 dict（可 JSON 序列化），关键字段：
  - `status`：`exact` / `downgraded` / `partial` / `unsupported`
  - `format` / `family`：选中的格式及其族（失败时为 `null`）
  - `exportable_fields`：可导出字段名（保持请求顺序）
  - `missing_fields`：缺失字段清单 `[{name, type, reason}]`
  - `options`：全部可用格式选项（含覆盖率、缺失字段、选择理由）
  - `trace`：协商步骤记录，便于审计

## 2. 协商顺序（严格按序执行）

1. **精确匹配**：`requested_format` 或其规范别名（如 `jsonl -> ndjson`）
   命中注册表 → 选中该格式。
   - 依据：用户明确请求且系统支持，无需任何降级；别名是同一格式的
     另一种写法，不算降级。
2. **同族降级**：请求格式已知但不支持（`xlsx/xls/ods`）→ 在其声明的
   数据族内按 `preference` 升序选首选格式。
   - 依据：只允许数据模型同构的族内降级（表格→表格、结构化→结构化），
     不跨族降级，避免把嵌套结构静默拍平成表格而用户不知情。
   - 族内排序依据是通用性/生态普及度（CSV 先于 TSV、JSON 先于 NDJSON），
     与实现成本无关。
3. **未知格式尽力匹配**：请求格式完全未知 → 在全部格式中按
   「覆盖率降序 → 族内 preference 升序 → 格式 id 字典序」选最优。
   - 依据：覆盖率最大化用户能拿到的数据；后两级是纯确定性 tie-break。
4. **部分满足**：上述任何一步选中的格式无法覆盖全部字段 →
   状态降为 `partial`，返回可导出字段 + 缺失字段清单。
   - 依据：部分数据优于直接报错；缺失清单让用户能据此调整请求。
5. **失败**：最优候选覆盖率为 0（任何格式都导不出任何字段）→
   `unsupported`，`format = null`，但 `options` 仍返回全部可用格式
   及各自的覆盖率/缺失原因，作为用户下一步选择的依据。

## 3. 确定性保证

- 所有排序键完整无歧义：`(−coverage, preference, format_id)`，
  任何并列都有最终的字典序兜底。
- 输出只含 list/dict/str/num，字段顺序来自请求顺序或显式排序，
  不依赖 set/dict 迭代顺序。
- 因此同一请求重复协商结果逐字节一致（`test_repeated_negotiation_is_identical`
  对每个用例重复 50 次验证）。

## 4. 对拍验证

`tests/reference_oracle.py` 是一份独立重写的参考实现（自带数据副本、
不复用库代码）。`tests/test_crosscheck.py` 在
13 种请求格式 × 339 种字段组合 = 18072 个用例上逐一比对
`(status, format, exportable, missing)` 四元组，全部一致；
数据副本与注册表的一致性也有专门测试防止漂移。
完整对拍数据见 `docs/crosscheck.json`。

## 5. 选项数据（registry）

| 格式   | 族      | preference | 支持的字段类型 |
|--------|---------|-----------|----------------|
| json   | json    | 5         | str,int,float,bool,date,datetime,object,array |
| ndjson | json    | 15        | 同 json |
| csv    | tabular | 10        | str,int,float,bool,date,datetime |
| tsv    | tabular | 20        | 同 csv |

- 别名：`jsonl -> ndjson`
- 已知不支持格式的降级族：`xlsx/xls/ods -> tabular`
- 已知但无人支持的字段类型：`binary`（触发 `partial`/`unsupported`）；
  其余未登记类型视为输入错误，抛 `ValueError`。
