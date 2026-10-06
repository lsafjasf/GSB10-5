"""Markdown 段落空白与换行规范化库（仅标准库）。

设计原则：规范化只改写"行内空白"，绝不增删行，
因此原文与结果可以逐行对齐比较，差异可逐条解释。

区域划分（判定依据见 RULES.md）：
  - 受保护区域（逐字节保留）：围栏代码块内容、缩进代码块内容。
  - 结构保留区域：引用块（保留行数与 '>' 标记，只规范化标记后的文本）、
    列表项（保留前导缩进与标记，只规范化标记后的空白）。
  - 参与规范化区域：普通段落文本、空行、行尾序列（CRLF）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Tuple

# ---- 规则代码（差异清单中使用） ----
R_EOL = "R1-EOL"          # CRLF -> LF（保护区外）
R_BLANK = "R2-BLANK"      # 纯空白行 -> 空行
R_TRIM = "R3-TRIM"        # 行尾空白删除（软换行前）
R_HARD = "R4-HARD"        # 硬换行标记规范为恰好两个空格
R_COLLAPSE = "R5-COLLAPSE"  # 行内连续空白折叠为单个空格
R_LEAD = "R6-LEAD"        # 段首无意义前导空白删除
R_QUOTE = "R7-QUOTE"      # 引用标记 '> ' 间距规范化
R_LIST = "R8-LIST"        # 列表标记后间距规范化

_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_INDENTED = re.compile(r"^(?:    |\t)")
_BLANK = re.compile(r"^[ \t]*$")
_QUOTE = re.compile(r"^( {0,3})>[ \t]?(.*)$")
_LIST = re.compile(r"^( {0,3})([-*+]|\d{1,9}[.)])([ \t]+)(.*)$")
_TRAIL = re.compile(r"[ \t]+$")
_LEAD = re.compile(r"^[ \t]+")
_RUN = re.compile(r"[ \t]+")


@dataclass(slots=True)
class Change:
    """一处差异：行号（1 起）、触发的规则、改前、改后。"""
    line: int
    rules: List[str]
    before: str
    after: str


@dataclass(slots=True)
class Result:
    text: str
    changes: List[Change]


@dataclass(slots=True)
class _Line:
    raw: str        # 不含 '\n' 的行内容（可能以 '\r' 结尾）
    nl: bool        # 源文中该行后是否跟有 '\n'
    protected: bool # 是否位于代码块保护区内


def _iter_lines(text: str):
    parts = text.split("\n")
    for part in parts[:-1]:
        yield part, True
    if parts[-1] != "":
        yield parts[-1], False


def _strip_cr(content: str) -> str:
    return content[:-1] if content.endswith("\r") else content


def _scan(text: str) -> List[_Line]:
    """把文本切成行并标注受保护行（代码块内容行）。"""
    items = list(_iter_lines(text))
    n = len(items)
    out: List[_Line] = []
    i = 0
    in_para = False  # 前一行是普通段落文本（缩进代码块不能打断段落）
    while i < n:
        content, nl = items[i]
        logical = _strip_cr(content)

        # 快路径：首字符不可能是围栏/缩进代码/空行的行，直接按普通行处理。
        if logical == "":
            out.append(_Line(content, nl, protected=False))
            in_para = False
            i += 1
            continue
        if logical[0] not in " `~\t":
            out.append(_Line(content, nl, protected=False))
            in_para = True
            i += 1
            continue

        m = _FENCE_OPEN.match(logical)
        if m:
            # 围栏行本身参与规范化；围栏之间的内容行受保护。
            out.append(_Line(content, nl, protected=False))
            i += 1
            fence = m.group(1)
            close_re = re.compile(
                r"^ {0,3}%s{%d,}[ \t]*$" % (re.escape(fence[0]), len(fence))
            )
            while i < n:
                c2, nl2 = items[i]
                if close_re.match(_strip_cr(c2)):
                    out.append(_Line(c2, nl2, protected=False))
                    i += 1
                    break
                out.append(_Line(c2, nl2, protected=True))
                i += 1
            in_para = False
            continue

        if not in_para and _INDENTED.match(logical):
            # 缩进代码块：连续的缩进行/空行都受保护，逐字节保留。
            while i < n:
                c2, nl2 = items[i]
                log2 = _strip_cr(c2)
                if _INDENTED.match(log2) or _BLANK.match(log2):
                    out.append(_Line(c2, nl2, protected=True))
                    i += 1
                else:
                    break
            in_para = False
            continue

        out.append(_Line(content, nl, protected=False))
        in_para = not _BLANK.match(logical)
        i += 1
    return out


def _normalize_text(s: str) -> Tuple[str, List[str]]:
    """规范化一段行内文本（不含行首结构标记）。返回 (新文本, 规则列表)。"""
    rules: List[str] = []
    hard = False
    m = _TRAIL.search(s)
    if m:
        run = m.group()
        # Markdown 硬换行：行尾 >= 2 个空格。规范为恰好两个空格；
        # 否则（软换行前的零散空白）直接删除。
        if run.count(" ") >= 2:
            hard = True
            rules.append(R_HARD)
        else:
            rules.append(R_TRIM)
        s = s[: m.start()]
    m = _LEAD.match(s)
    if m:
        rules.append(R_LEAD)
        s = s[m.end():]
    collapsed = _RUN.sub(" ", s)
    if collapsed != s:
        rules.append(R_COLLAPSE)
        s = collapsed
    if hard:
        s += "  "
    return s, rules


def _normalize_line(raw: str) -> Tuple[str, List[str]]:
    rules: List[str] = []
    line = raw
    if line.endswith("\r"):
        line = line[:-1]
        rules.append(R_EOL)

    # 快路径：首字符排除空行/围栏/引用/列表后，直接按段落文本处理。
    if line and line[0] not in " \t>*+-`~0123456789":
        new, sub = _normalize_text(line)
        return new, rules + sub

    # 围栏开/闭行不是段落文本：其行尾空白是语法允许的尾随空白，
    # 不构成硬换行，直接删除；info string 内部一律不动。
    if _FENCE_OPEN.match(line):
        new = _TRAIL.sub("", line)
        if new != line:
            rules.append(R_TRIM)
        return new, rules

    if _BLANK.match(line):
        if line != "":
            rules.append(R_BLANK)
        return "", rules

    m = _QUOTE.match(line)
    if m:
        prefix = m.group(1) + ">"
        content = m.group(2)
        if content.strip(" \t") == "":
            new = prefix
            if content:
                rules.append(R_TRIM)
        else:
            body, sub = _normalize_text(content)
            rules.extend(sub)
            new = prefix + " " + body
        if new != line and not rules:
            rules.append(R_QUOTE)
        return new, rules

    m = _LIST.match(line)
    if m:
        indent, marker, sep, rest = m.groups()
        body, rules2 = _normalize_text(rest)
        rules.extend(rules2)
        if not rest and sep:
            rules.append(R_TRIM)
        new = indent + marker + (" " + body if body else "")
        if new != line and not rules:
            rules.append(R_LIST)
        return new, rules

    return _normalize_text(line)


def normalize(text: str) -> Result:
    """规范化整篇文本，返回新文本与逐条差异清单。"""
    out_parts: List[str] = []
    changes: List[Change] = []
    for lineno, ln in enumerate(_scan(text), 1):
        if ln.protected:
            out_parts.append(ln.raw + ("\n" if ln.nl else ""))
            continue
        new, rules = _normalize_line(ln.raw)
        if new != ln.raw:
            changes.append(Change(lineno, rules, ln.raw, new))
        out_parts.append(new + ("\n" if ln.nl else ""))
    return Result("".join(out_parts), changes)


def protected_lines(text: str) -> List[str]:
    """返回所有受保护行（代码块内容）的原始字节串，用于验证。"""
    return [ln.raw for ln in _scan(text) if ln.protected]
