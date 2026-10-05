"""版本模式定义 —— 回滚验证的四个兼容面。

兼容面及依据：
1. 数据格式 (fields)：记录为 JSON Lines，每行一个对象。旧版本按字段名/类型/必填性
   解析，字段结构不一致会直接导致反序列化或业务逻辑失败。
2. 枚举取值 (enum values)：status 的取值集合是闭集，旧版本代码（如状态机、
   排序映射）对未知取值没有分支，属于典型的回滚破坏点。
3. 索引结构 (index)：v1 使用单文件 flat-hash 索引（id -> 行偏移），v2 改为
   分片 sharded-hash。索引格式/版本号不匹配时旧版本无法定位记录。
4. 接口契约 (contract)：旧版本业务函数（render_label / status_rank）对记录
   的隐含假设——必需键存在、枚举可映射。契约通过“实际执行旧代码”来验证，
   而不是只做静态比对。
"""

STATUS_V1 = ("active", "inactive", "banned")
STATUS_V2 = STATUS_V1 + ("pending",)


def _fields(extra=None, status_values=STATUS_V1):
    fields = {
        "id":     {"type": "int",  "required": True},
        "name":   {"type": "str",  "required": True},
        "status": {"type": "enum", "required": True, "values": tuple(status_values)},
        "age":    {"type": "int",  "required": False},
        "email":  {"type": "str",  "required": False},
    }
    if extra:
        fields.update(extra)
    return fields


SCHEMA_V1 = {
    "version": 1,
    "fields": _fields(),
    # 旧版本对未知字段的策略：忽略（大多数 JSON 解析器的默认行为）
    "unknown_field_policy": "ignore",
    "index": {"format": "flat-hash", "version": 1},
}

SCHEMA_V2 = {
    "version": 2,
    "fields": _fields(
        extra={
            "phone": {"type": "str",  "required": False},
            "tags":  {"type": "list", "required": False},
        },
        status_values=STATUS_V2,
    ),
    "unknown_field_policy": "ignore",
    "index": {"format": "sharded-hash", "version": 2},
}
