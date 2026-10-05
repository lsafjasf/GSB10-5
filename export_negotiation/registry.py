"""选项数据：导出格式能力注册表。

数据即配置：每个格式声明自己的类型、优先级、支持的字段类型、
降级链（按序尝试）与别名。所有规则文档见 RULES.md。
"""

# 字段类型全集（数据模型中可能出现的字段类型）
FIELD_TYPES = (
    "string", "number", "boolean", "date",
    "nested", "binary", "null", "richtext",
)

# 格式能力注册表（dict 字面量，插入顺序即注册顺序）
FORMATS = {
    "json": {
        "kind": "text",
        "priority": 100,
        "field_types": ("string", "number", "boolean", "date", "nested", "binary", "null"),
        "fallbacks": [],
        "aliases": ["json5"],
        "reason": "通用结构化格式，支持嵌套与二进制（base64），无富文本标记能力",
    },
    "csv": {
        "kind": "text",
        "priority": 90,
        "field_types": ("string", "number", "boolean", "date", "null"),
        "fallbacks": [],
        "aliases": [],
        "reason": "表格数据标准格式，兼容 Excel/数据库导入",
    },
    "tsv": {
        "kind": "text",
        "priority": 80,
        "field_types": ("string", "number", "boolean", "date"),
        "fallbacks": ["csv"],
        "aliases": [],
        "reason": "制表符分隔的表格格式；无标准 null 字面量，遇 null 字段降级 csv",
    },
    "xlsx": {
        "kind": "binary",
        "priority": 70,
        "field_types": ("string", "number", "boolean", "date", "null"),
        "fallbacks": ["csv"],
        "aliases": [],
        "reason": "电子表格格式，保留类型与日期格式",
    },
    "xml": {
        "kind": "text",
        "priority": 60,
        "field_types": ("string", "number", "boolean", "date", "nested", "null"),
        "fallbacks": ["json"],
        "aliases": [],
        "reason": "结构化标记格式，支持嵌套结构，不支持二进制",
    },
    "yaml": {
        "kind": "text",
        "priority": 50,
        "field_types": ("string", "number", "boolean", "date", "nested", "null"),
        "fallbacks": ["json"],
        "aliases": ["yml"],
        "reason": "人类可读的结构化格式，支持嵌套结构",
    },
    "html": {
        "kind": "text",
        "priority": 40,
        "field_types": ("string", "number", "boolean", "date", "null", "richtext"),
        "fallbacks": ["csv"],
        "aliases": [],
        "reason": "网页展示格式，支持富文本渲染",
    },
    "pdf": {
        "kind": "binary",
        "priority": 30,
        "field_types": ("string", "number", "boolean", "date", "richtext"),
        "fallbacks": ["html"],
        "aliases": [],
        "reason": "版式固定的文档格式，适合打印与归档",
    },
}

# 别名 -> 规范格式名（由 FORMATS 自动生成，勿手改）
ALIASES = {
    alias: name
    for name, spec in FORMATS.items()
    for alias in spec["aliases"]
}
