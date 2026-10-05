"""printf 风格占位符的扫描与解析（仅使用标准库）。

支持的三种寻址方式（同一字符串内不可混用）：

- 命名参数：``%(name)s``
- 位置参数：``%2$s``（POSIX 风格，编号从 1 开始）
- 裸占位符：``%s``（按出现顺序取值）

``%%`` 是普通百分号字面量，不算占位符。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

#: 支持的转换字符（% 本身表示字面量百分号）
CONVERSIONS = frozenset("diouxXeEfFgGcrsa%")

_TOKEN_RE = re.compile(
    r"""
    %
    (?:\((?P<key>[^()]*)\))?        # 命名键 %(name)
    (?P<idx>\d+\$)?                 # POSIX 位置参数 %2$（必须先于宽度尝试）
    (?P<flags>[-+\#0 ]*)            # 标志 - + # 0 空格
    (?P<width>\*|\d*)               # 宽度（* 为动态宽度，判 invalid）
    (?P<prec>\.(?:\*|\d*))?         # 精度 .N（.* 判 invalid）
    (?P<length>hh|ll|[hlL])?        # 长度修饰符（解析但渲染时忽略）
    (?P<conv>[A-Za-z%])             # 转换字符
    """,
    re.VERBOSE,
)


@dataclass(frozen=True)
class Token:
    """一个解析成功的占位符。"""

    raw: str
    start: int
    end: int
    key: Optional[str]
    index: Optional[int]
    ordinal: Optional[int]
    flags: str
    width: str
    precision: Optional[str]
    length: str
    conversion: str

    @property
    def style(self) -> str:
        if self.key is not None:
            return "named"
        if self.index is not None:
            return "positional"
        return "bare"

    @property
    def ident(self) -> str:
        """规范身份：集合比对使用的键（不含宽度/精度/标志/长度）。"""
        if self.key is not None:
            return "%({}){}".format(self.key, self.conversion)
        if self.index is not None:
            return "%{}${}".format(self.index, self.conversion)
        return "%" + self.conversion

    @property
    def spec_key(self) -> str:
        """完整格式说明，用于可选的格式漂移提示。"""
        prec = "-" if self.precision is None else "." + self.precision
        return "{}|{}|{}|{}|{}".format(
            self.ident, self.flags or "-", self.width or "-", prec, self.length or "-"
        )


@dataclass(frozen=True)
class InvalidItem:
    start: int
    end: int
    text: str
    reason: str


@dataclass(frozen=True)
class Chunk:
    text: str
    token: Optional[Token] = None
    invalid: Optional[InvalidItem] = None

    @property
    def is_literal(self) -> bool:
        return self.token is None and self.invalid is None


@dataclass(frozen=True)
class ScanResult:
    chunks: Tuple[Chunk, ...]
    tokens: Tuple[Token, ...]
    invalid: Tuple[InvalidItem, ...]


def _check(conv, key, idx, flags, width, prec, length) -> Optional[str]:
    """对正则匹配出的候选占位符做合法性检查，返回 None 表示合法。"""
    if conv not in CONVERSIONS:
        return "不支持的转换字符 %{}".format(conv)
    if conv == "%":
        if key is not None or idx or flags or width or prec is not None or length:
            return "%% 字面量不允许带任何修饰"
        return None
    if width == "*" or (prec is not None and "*" in prec):
        return "不支持动态宽度/精度（*）"
    if key is not None and key == "":
        return "空命名键 %()"
    if key is not None and idx:
        return "命名参数与位置参数不能同时出现"
    if idx is not None and int(idx[:-1]) == 0:
        return "位置参数编号从 1 开始"
    return None


def scan(text: str) -> ScanResult:
    """把字符串切成字面量 / 占位符 / 非法片段三类 chunk。"""
    chunks: List[Chunk] = []
    tokens: List[Token] = []
    invalids: List[InvalidItem] = []
    literal_start = 0
    ordinal = 0
    style_seen: Optional[str] = None
    i = 0
    n = len(text)

    def flush_literal(end: int) -> None:
        if end > literal_start:
            chunks.append(Chunk(text=text[literal_start:end]))

    while i < n:
        if text[i] != "%":
            i += 1
            continue
        m = _TOKEN_RE.match(text, i)
        if m is None:
            item = InvalidItem(i, i + 1, text[i], "非法的 % 序列（悬空 % 或结构不完整）")
            flush_literal(i)
            chunks.append(Chunk(text=item.text, invalid=item))
            invalids.append(item)
            i += 1
            literal_start = i
            continue
        raw = m.group(0)
        conv = m.group("conv")
        key = m.group("key")
        idx = m.group("idx")
        flags = m.group("flags")
        width = m.group("width")
        prec = m.group("prec")
        length = m.group("length") or ""

        reason = _check(conv, key, idx, flags, width, prec, length)
        if reason is None and conv == "%":
            # 字面量百分号：还原为单个 %
            flush_literal(i)
            chunks.append(Chunk(text="%"))
            i = m.end()
            literal_start = i
            continue
        if reason is None:
            style = "named" if key is not None else ("positional" if idx else "bare")
            if style_seen is None:
                style_seen = style
            elif style != style_seen:
                reason = "同一字符串中混用不同的寻址方式（named/positional/bare）"
        if reason is not None:
            item = InvalidItem(i, m.end(), raw, reason)
            flush_literal(i)
            chunks.append(Chunk(text=raw, invalid=item))
            invalids.append(item)
            i = m.end()
            literal_start = i
            continue
        if key is None and idx is None:
            ordinal += 1
            tok_ordinal: Optional[int] = ordinal
        else:
            tok_ordinal = None
        token = Token(
            raw=raw,
            start=i,
            end=m.end(),
            key=key,
            index=int(idx[:-1]) if idx else None,
            ordinal=tok_ordinal,
            flags=flags,
            width=width,
            precision=prec[1:] if prec is not None else None,
            length=length,
            conversion=conv,
        )
        flush_literal(i)
        chunks.append(Chunk(text=raw, token=token))
        tokens.append(token)
        i = m.end()
        literal_start = i

    flush_literal(n)
    return ScanResult(tuple(chunks), tuple(tokens), tuple(invalids))
