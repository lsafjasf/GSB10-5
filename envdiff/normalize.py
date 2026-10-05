"""值规范化与等价比较。

消除"写法不同但语义相同"的差异：大小写、空白字符、引号、数值写法、
单位换算（存储 / 时间）、布尔写法、列表顺序与分隔符。
"""
from __future__ import annotations

import re

_BOOL_TRUE = {"true", "yes", "on", "enable", "enabled"}
_BOOL_FALSE = {"false", "no", "off", "disable", "disabled"}

# 存储单位（二进制，1KB = 1024B）。单字母 k/m/g/t 按存储单位处理。
_SIZE_UNITS = {
    "b": 1,
    "kb": 1024, "mb": 1024 ** 2, "gb": 1024 ** 3, "tb": 1024 ** 4,
    "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3, "t": 1024 ** 4,
}
# 时间单位，统一折算为毫秒。
_TIME_UNITS = {"ms": 1, "s": 1000, "sec": 1000, "min": 60000, "h": 3600000}

_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)$")
_NUMBER_UNIT_RE = re.compile(r"^([+-]?(?:\d+(?:\.\d+)?|\.\d+))\s*([A-Za-z]{1,4})$")
_LIST_SPLIT_RE = re.compile(r"[,;|]")


def _strip_quotes(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"', "`"):
        return text[1:-1].strip()
    return text


def canonical(value) -> tuple:
    """返回值的规范化形式，规范化形式相等即视为语义等价。"""
    text = _strip_quotes(str(value))
    collapsed = " ".join(text.split())
    low = collapsed.lower()
    if low in _BOOL_TRUE:
        return ("bool", True)
    if low in _BOOL_FALSE:
        return ("bool", False)
    if _NUMBER_RE.match(collapsed):
        return ("num", float(collapsed))
    match = _NUMBER_UNIT_RE.match(collapsed)
    if match:
        number, unit = float(match.group(1)), match.group(2).lower()
        if unit in _SIZE_UNITS:
            return ("size", number * _SIZE_UNITS[unit])
        if unit in _TIME_UNITS:
            return ("time", number * _TIME_UNITS[unit])
    if _LIST_SPLIT_RE.search(collapsed):
        items = [canonical(part) for part in _LIST_SPLIT_RE.split(collapsed) if part.strip()]
        return ("list", tuple(sorted(items)))
    return ("str", collapsed.casefold())


def equivalence_reasons(raw_a: str, raw_b: str) -> list[str]:
    """a、b 规范化后等价但原文不同，说明是哪些写法差异导致的。"""
    reasons = []
    if _strip_quotes(raw_a) != raw_a.strip() or _strip_quotes(raw_b) != raw_b.strip():
        reasons.append("引号")
    if " ".join(raw_a.split()) != raw_a or " ".join(raw_b.split()) != raw_b:
        reasons.append("空白字符")
    kind = canonical(raw_a)[0]
    if kind == "bool":
        reasons.append("布尔写法")
    elif kind == "num":
        reasons.append("数值写法")
    elif kind in ("size", "time"):
        reasons.append("单位写法")
    elif kind == "list":
        reasons.append("列表顺序/分隔符")
    elif kind == "str" and raw_a.strip().casefold() == raw_b.strip().casefold() \
            and raw_a.strip() != raw_b.strip():
        reasons.append("大小写")
    return reasons or ["写法"]
