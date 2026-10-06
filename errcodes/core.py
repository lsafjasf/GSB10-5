"""错误码组成规则与解析。

错误码为 8 位字符串，形如 ``E0201001``：

    E AA CC NNN
    │  │  │  └─ NNN：3 位序号（001-999），模块内按错误类别顺序分配
    │  │  └── CC ：2 位错误类别（01-99），见 CATEGORY_NAMES
    │  └───── AA ：2 位业务域（01-99），每个模块只能使用自己注册的域
    └──────── 固定前缀 E（Error）

分配依据：
- 业务域按系统边界划分（01 认证 / 02 支付 / 03 订单 / 04 库存 / 90 通用），
  域与模块一一绑定，从编码层面杜绝两个模块撞码；
- 类别按错误性质划分，01-99 均合法，常用类别在 CATEGORY_NAMES 登记了
  显示名（01 参数、02 鉴权、03 资源、04 状态、05 依赖、09 系统），
  便于前端按类别做统一处理；
- 序号在模块内按定义顺序递增，001-899 为常规码，900-999 保留给兜底/未知错误。
"""

import re

PREFIX = "E"
CODE_PATTERN = re.compile(r"^E(\d{2})(\d{2})(\d{3})$")

CATEGORY_NAMES = {
    "01": "参数错误",
    "02": "鉴权错误",
    "03": "资源错误",
    "04": "状态错误",
    "05": "依赖错误",
    "09": "系统错误",
}

# 模块 -> 允许使用的业务域。新增模块必须在此登记，登记即占用该域。
AREA_MAP = {
    "auth": "01",
    "payment": "02",
    "order": "03",
    "inventory": "04",
    "common": "90",
}

RESERVED_SERIAL_MIN = 900  # 900-999 为保留序号（兜底/未知错误）


def make_code(area, category, serial):
    """由 域/类别/序号 组装错误码。"""
    return "%s%02d%02d%03d" % (PREFIX, int(area), int(category), int(serial))


def parse_code(code):
    """解析错误码，返回 (area, category, serial) 字符串三元组；格式非法返回 None。"""
    match = CODE_PATTERN.match(code)
    if not match:
        return None
    return match.group(1), match.group(2), match.group(3)


def is_valid_code(code):
    return parse_code(code) is not None


def category_name(code):
    parsed = parse_code(code)
    if not parsed:
        return None
    return CATEGORY_NAMES.get(parsed[1], "未分类")
