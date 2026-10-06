"""日志级别定义。数值越大级别越高（日志越少）。"""

DEBUG = 10
INFO = 20
WARN = 30
ERROR = 40
OFF = 50

LEVEL_ORDER = (DEBUG, INFO, WARN, ERROR, OFF)

_NAMES = {
    DEBUG: "DEBUG",
    INFO: "INFO",
    WARN: "WARN",
    ERROR: "ERROR",
    OFF: "OFF",
}
_BY_NAME = {v: k for k, v in _NAMES.items()}


def coerce(level):
    """把字符串或整数统一成内部整数级别。"""
    if isinstance(level, str):
        key = level.upper()
        if key not in _BY_NAME:
            raise ValueError("unknown level: %r" % level)
        return _BY_NAME[key]
    value = int(level)
    if value not in _NAMES:
        raise ValueError("unknown level: %r" % level)
    return value


def level_name(level):
    """级别转可读名称；None 表示无覆盖。"""
    if level is None:
        return "-"
    return _NAMES[coerce(level)]


def next_higher(level):
    """返回更静默的一级（减少日志量），已是最高返回 None。"""
    value = coerce(level)
    index = LEVEL_ORDER.index(value)
    if index + 1 >= len(LEVEL_ORDER):
        return None
    return LEVEL_ORDER[index + 1]


def next_lower(level):
    """返回更详细的一级（增加日志量），已是最低返回 None。"""
    value = coerce(level)
    index = LEVEL_ORDER.index(value)
    if index == 0:
        return None
    return LEVEL_ORDER[index - 1]
