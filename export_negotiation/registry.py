"""格式注册表：唯一的数据源（options data）。

所有协商决策只依赖本模块的静态数据，保证确定性。
排序约定（依据见 docs/NEGOTIATION_RULES.md）：
- 同一族内 fallback 顺序按 ``preference`` 升序，数值越小越优先；
  依据是通用性/生态普及度，而非实现成本。
- 并列时按格式 id 字典序，彻底消除不确定性。
"""

# 每种格式声明：族、族内优先级、可无损导出的字段类型集合。
FORMATS = {
    "json": {
        "family": "json",
        "preference": 5,
        "field_types": frozenset(
            {"str", "int", "float", "bool", "date", "datetime", "object", "array"}
        ),
        "note": "通用结构化格式，类型保真度最高",
    },
    "ndjson": {
        "family": "json",
        "preference": 15,
        "field_types": frozenset(
            {"str", "int", "float", "bool", "date", "datetime", "object", "array"}
        ),
        "note": "行式 JSON，适合流式/大数据量导出",
    },
    "csv": {
        "family": "tabular",
        "preference": 10,
        "field_types": frozenset({"str", "int", "float", "bool", "date", "datetime"}),
        "note": "通用表格格式，仅支持标量列",
    },
    "tsv": {
        "family": "tabular",
        "preference": 20,
        "field_types": frozenset({"str", "int", "float", "bool", "date", "datetime"}),
        "note": "制表符分隔表格，兼容性次于 CSV",
    },
}

# 别名 -> 规范格式 id。别名命中视为“精确匹配”（同一格式，不算降级）。
ALIASES = {
    "jsonl": "ndjson",
}

# 已知但不支持的请求格式 -> 降级族。
# 依据：数据模型同构才允许降级（表格 -> 表格，结构化 -> 结构化），
# 不跨族降级，避免静默丢失嵌套结构。
FALLBACK_FAMILY = {
    "xlsx": "tabular",
    "xls": "tabular",
    "ods": "tabular",
}
