"""wordfreq.py —— 统一口径的词频统计库（仅标准库）。

公开 API：
    tokenize(text, spec=UNICODE_COMPOUND) -> list[str]
    iter_tokens(text, spec=UNICODE_COMPOUND) -> Iterator[str]
    count_tokens(text, spec=UNICODE_COMPOUND) -> collections.Counter
    to_table(counter) -> list[tuple[str, int]]

两套口径：
    ASCII_SPLIT       极简口径：只认 ASCII 的 A-Z / a-z / 0-9，其余全部是分隔符。
                      词边界完全由正则 [A-Za-z0-9]+ 决定，lower() 归一小写。
    UNICODE_COMPOUND  通用口径：Unicode 字母/数字成词；连字符在词内部保留；
                      汉字按单字成词，且汉字与拉丁字母/数字相邻时强制断开
                      （不做任何词库/结巴式中文分词）；NFKC 兼容分解后
                      casefold() 归一小写。

确定性：同输入 + 同口径，每次输出序列与计数完全一致；
       to_table 按 (-次数, 词) 排序，保证跨机器 / 跨运行结果可复现。
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import Iterator

ASCII_SPLIT = "ascii_split"
UNICODE_COMPOUND = "unicode_compound"
SPECS = (ASCII_SPLIT, UNICODE_COMPOUND)

_ASCII_WORD_RE = re.compile(r"[A-Za-z0-9]+")


def _escape_range(lo: int, hi: int) -> str:
    def esc(code: int) -> str:
        return "\\U%08X" % code if code > 0xFFFF else "\\u%04X" % code

    return esc(lo) if lo == hi else esc(lo) + "-" + esc(hi)


_HAN_RANGES = (
    (0x2E80, 0x2EFF),    # CJK 部首补充
    (0x2F00, 0x2FDF),    # 康熙部首
    (0x3005, 0x3005),    # 々（汉字叠字符号，归入汉字类）
    (0x3007, 0x3007),    # 〇（汉字数字零）
    (0x3021, 0x3029),    # 苏州码子（数字，按汉字类处理）
    (0x3038, 0x303B),    # 〸〹〺
    (0x3400, 0x4DBF),    # CJK 扩展 A
    (0x4E00, 0x9FFF),    # CJK 统一表意文字（基本汉字）
    (0xF900, 0xFAFF),    # CJK 兼容表意文字
    (0x20000, 0x2A6DF),  # CJK 扩展 B
    (0x2A700, 0x2EBEF),  # CJK 扩展 C/D/E/F
    (0x2F800, 0x2FA1F),  # CJK 兼容补充
    (0x30000, 0x3134F),  # CJK 扩展 G
)

_HAN_RE = re.compile(
    "[" + "".join(_escape_range(lo, hi) for lo, hi in _HAN_RANGES) + "]"
)


def is_han(ch: str) -> bool:
    """判断单个字符是否按“汉字”处理（单字成词的范围）。"""
    return bool(_HAN_RE.match(ch))


def _is_word_char(ch: str) -> bool:
    """Unicode 口径下的字母 / 数字判定。

    Unicode 类别以 L 开头（Ll/Lu/Lt/Lm/Lo，汉字 Lo 单独交给 is_han），
    或类别以 N 开头（Nd/Nl/No，如 ①、罗马数字）。
    组合符号 M* 不单独成词，只允许附加在词内前一个字符上（状态机里处理）。
    """
    cat = unicodedata.category(ch)
    return cat[0] in ("L", "N")


def _is_dash(ch: str) -> str | None:
    """词内连字符判定；是则返回归一化后的连字符，否则返回 None。

    输入应已过 NFKC。统一为 ASCII '-'(U+002D)：
      - U+002D 连字符-减号
      - U+2010 连字号（hyphen）
      - U+2011 不换行连字号
      - U+2012 figure dash / U+2013 en dash / U+2014 em dash
      - U+2015 horizontal bar / U+2212 减号
    下划线 U+005F 不算连字符（按分隔符处理，与 ascii_split 口径一致）。
    """
    if ch == "-" or ch in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212":
        return "-"
    return None


def iter_tokens(text: str, spec: str = UNICODE_COMPOUND) -> Iterator[str]:
    """惰性产出分词结果。spec 取 ASCII_SPLIT 或 UNICODE_COMPOUND。"""
    if spec == ASCII_SPLIT:
        for match in _ASCII_WORD_RE.finditer(text):
            yield match.group().lower()
        return
    if spec != UNICODE_COMPOUND:
        raise ValueError("未知口径 %r，可选：%s" % (spec, ", ".join(SPECS)))

    # 1) NFKC 兼容分解：全角→半角、①→1、ﬁ→fi、罗马数字→字母序列等。
    text = unicodedata.normalize("NFKC", text)

    pending: list[str] = []       # 当前词的已归一化字符（含词内连字符）
    last_kind: str | None = None  # "han" / "latin"，用于不同文字体系间强制断词

    def flush() -> Iterator[str]:
        # 去掉词尾残留的连字符（如 "well-" → "well"）；
        # 词中间的连续连字符保留原样（如 "a--b" → "a--b"）。
        while pending and pending[-1] == "-":
            pending.pop()
        if pending:
            # NFC 合成：café 的预组合/分解两种 Unicode 写法归一到同一词形。
            yield unicodedata.normalize("NFC", "".join(pending).casefold())

    for ch in text:
        if is_han(ch):
            kind = "han"
        elif _is_word_char(ch):
            kind = "latin"
        elif unicodedata.category(ch)[0] == "M" and pending:
            # 组合符号（如 U+0301）附属于前一个字符，不改变 last_kind。
            pending.append(ch.casefold())
            continue
        else:
            dash = _is_dash(ch)
            if dash is not None and pending:
                # 2) 连字符：先收进 pending（允许连续，如 a--b），
                #    若其后没有词字符，flush 时从词尾丢弃。
                pending.append(dash)
                continue
            kind = None

        if kind is None:
            yield from flush()
            pending = []
            last_kind = None
            continue

        if last_kind is not None and kind != last_kind:
            # 3) 汉字与拉丁/数字相邻：强制断开，互不粘连。
            yield from flush()
            pending = []

        if kind == "han":
            # 汉字单字成词：缓冲此时必为空或残留连字符，先 flush 再直接产出单字。
            yield from flush()
            pending = []
            yield ch.casefold()
            last_kind = "han"
        else:
            pending.append(ch.casefold())
            last_kind = "latin"

    yield from flush()


def tokenize(text: str, spec: str = UNICODE_COMPOUND) -> list[str]:
    """返回分词列表（口径见模块文档字符串）。"""
    return list(iter_tokens(text, spec))


def count_tokens(text: str, spec: str = UNICODE_COMPOUND) -> Counter:
    """按口径统计词频，返回 collections.Counter（惰性分词，不全量保存词表）。"""
    return Counter(iter_tokens(text, spec))


def to_table(counter: Counter) -> list[tuple[str, int]]:
    """确定性排序：次数降序，次数相同按词 Unicode 码位字典序升序。"""
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))


def format_table(counter: Counter, limit: int | None = None) -> str:
    """把词频结果渲染成定宽文本表，便于对照与存档。"""
    rows = to_table(counter)
    if limit is not None:
        rows = rows[:limit]
    if not rows:
        return "(空)"
    width = max(len(word) for word, _ in rows)
    return "\n".join("%-*s  %d" % (width, word, n) for word, n in rows)
