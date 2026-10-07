"""mdnorm — 段落与换行规范化库（仅标准库）。

区域判定（依据 CommonMark 结构规则的保守子集）：
  FENCE_CODE  : 围栏代码块（``` 或 ~~~，最多 3 个前导空格），逐字节保留
  INDENT_CODE : 缩进代码块（4 空格/Tab，且不能打断段落），逐字节保留
  QUOTE       : 引用块行（最多 3 个前导空格后跟 >），原样保留（含换行）
  BLANK       : 空行（仅含空白），连续空行折叠为一个
  PARAGRAPH   : 其余行，参与段落规范化

段落规范化规则：
  1. 行内连续空格/Tab 折叠为一个空格；去掉行首行尾空白
  2. 软换行（普通行末换行）-> 以一个空格连接相邻行
  3. 硬换行保留换行，标记统一为行末两个空格：
       - 行末 >=2 个空格（CommonMark hard break）
       - 行末反斜杠（CommonMark backslash hard break），保留反斜杠形态
  4. 连续空行折叠为一个；全文以单个 \n 结尾
"""

from __future__ import annotations

import dataclasses
import hashlib
from typing import List, Tuple, Union


@dataclasses.dataclass(frozen=True)
class Region:
    kind: str                 # FENCE_CODE / INDENT_CODE / QUOTE / BLANK / PARAGRAPH
    start: int                # 起始行号（1-based，含）
    end: int                  # 结束行号（1-based，含）
    raw: str                  # 原始文本（含自身换行符）


@dataclasses.dataclass
class DiffEntry:
    lineno: int               # 原文起始行号
    kind: str                 # 变更类型，见 RULES.md
    original: str
    normalized: str
    explanation: str


# ---------------------------------------------------------------- 解析

def _split_lines_keepends(text: str) -> List[str]:
    lines = text.splitlines(keepends=True)
    if text and not text.endswith(("\n",)):
        lines[-1] = lines[-1]  # 末行无换行，保持原样
    return lines


def _fence_info(line_body: str):
    """若行为围栏开头，返回 (字符, 长度)；否则 None。"""
    stripped = line_body.lstrip(" ")
    indent = len(line_body) - len(stripped)
    if indent > 3 or not stripped:
        return None
    if stripped[0] in "`~":
        ch = stripped[0]
        n = 0
        for c in stripped:
            if c == ch:
                n += 1
            else:
                break
        if n >= 3:
            if ch == "`" and "`" in stripped[n:]:
                return None
            return ch, n
    return None


def parse_regions(text: str) -> List[Region]:
    lines = _split_lines_keepends(text)
    regions: List[Region] = []
    i = 0
    n = len(lines)

    def body_of(ln: str) -> str:
        return ln[:-1] if ln.endswith("\n") else ln

    def is_blank(ln: str) -> bool:
        return body_of(ln).strip() == ""

    def is_quote(ln: str) -> bool:
        b = body_of(ln).lstrip(" ")
        return len(body_of(ln)) - len(b) <= 3 and b.startswith(">")

    def is_indent_code(ln: str) -> bool:
        b = body_of(ln)
        return b.startswith("    ") or b.startswith("\t")

    while i < n:
        line = lines[i]

        # 围栏代码块
        fi = _fence_info(body_of(line))
        if fi:
            ch, fence_len = fi
            j = i + 1
            while j < n:
                b2 = body_of(lines[j]).strip()
                if b2[:fence_len].count(ch) == len(b2[:fence_len]) and len(b2) >= fence_len and b2.startswith(ch):
                    j += 1
                    break
                j += 1
            regions.append(Region("FENCE_CODE", i + 1, j, "".join(lines[i:j])))
            i = j
            continue

        if is_blank(line):
            j = i
            while j < n and is_blank(lines[j]):
                j += 1
            regions.append(Region("BLANK", i + 1, j, "".join(lines[i:j])))
            i = j
            continue

        if is_quote(line):
            j = i
            while j < n:
                if is_quote(lines[j]):
                    j += 1
                elif is_blank(lines[j]):
                    # lazy/blank 归并到引用块直至再次出现非引用非空行
                    k = j
                    while k < n and is_blank(lines[k]):
                        k += 1
                    if k < n and is_quote(lines[k]):
                        j = k
                    else:
                        break
                else:
                    break
            regions.append(Region("QUOTE", i + 1, j, "".join(lines[i:j])))
            i = j
            continue

        # 段落（进行中）或缩进代码块（段落外）
        para_open = bool(regions and regions[-1].kind == "PARAGRAPH"
                         and regions[-1].end == i)
        if not para_open and is_indent_code(line):
            j = i
            while j < n and (is_indent_code(lines[j]) or is_blank(lines[j])):
                # 空行后若非缩进代码则结束（把空行还回）
                if is_blank(lines[j]):
                    k = j
                    while k < n and is_blank(lines[k]):
                        k += 1
                    if k < n and is_indent_code(lines[k]):
                        j = k
                    else:
                        break
                else:
                    j += 1
            regions.append(Region("INDENT_CODE", i + 1, j, "".join(lines[i:j])))
            i = j
            continue

        # 普通段落行：吃到下一个空行/结构行为止
        j = i + 1
        while j < n:
            nxt_body = body_of(lines[j])
            if is_blank(lines[j]) or is_quote(lines[j]) or _fence_info(nxt_body):
                break
            # 段落外出现缩进代码即终止段落
            if is_indent_code(lines[j]):
                break
            j += 1
        if para_open:
            prev = regions[-1]
            merged = Region("PARAGRAPH", prev.start, j, prev.raw + "".join(lines[i:j]))
            regions[-1] = merged
        else:
            regions.append(Region("PARAGRAPH", i + 1, j, "".join(lines[i:j])))
        i = j

    return regions


# ---------------------------------------------------------------- 段落规范化

def _strip_line_eol(body: str) -> Tuple[str, str, str]:
    """返回 (去掉行末空白的内容, 行末空白串, 是否以反斜杠硬换行结尾)。"""
    rstripped = body.rstrip(" \t")
    trailing = body[len(rstripped):]
    backslash = rstripped.endswith("\\")
    return rstripped, trailing, backslash


def normalize_paragraph(raw: str, base_lineno: int,
                        collect_diffs: bool = True) -> Tuple[str, List[DiffEntry]]:
    lines = _split_lines_keepends(raw)
    pieces: List[str] = []
    diffs: List[DiffEntry] = []
    last = len(lines) - 1

    for idx, ln in enumerate(lines):
        body = ln[:-1] if ln.endswith("\n") else ln
        has_nl = ln.endswith("\n")
        stripped, trailing, backslash = _strip_line_eol(body)
        collapsed = " ".join(stripped.split())   # 行内空白折叠（反斜杠随内容保留）
        is_last = idx == last
        # 硬换行仅对非末行有意义；末行的行末空白一律删除
        hard = not is_last and (backslash or len(trailing) >= 2)
        marker = "  " if (hard and not backslash) else ""
        pieces.append(collapsed + marker)

        if collect_diffs and collapsed + marker != body:
            kind = "HARD_BREAK_MARKER" if hard else "WHITESPACE"
            expl = ("硬换行：换行保留，行末空白统一为两个空格的硬换行标记"
                    if hard else
                    "行内空白折叠/首尾空白删除：连续空格或 Tab 合并为单个空格")
            diffs.append(DiffEntry(
                base_lineno + idx, kind, body, collapsed + marker, expl))

        if has_nl:
            if is_last:
                pieces.append("\n")
            elif hard:
                pieces.append("\n")
            else:
                pieces.append(" ")
                if not collect_diffs:
                    continue
                diffs.append(DiffEntry(
                    base_lineno + idx, "SOFT_BREAK_JOIN",
                    body + "\u23ce\n",
                    collapsed + " \u23ce",
                    "软换行：换行替换为单个空格，与下一行连接"))

    return "".join(pieces), diffs


def normalize(text: str, collect_diffs: bool = True) -> Tuple[str, List[DiffEntry]]:
    regions = parse_regions(text)
    out: List[str] = []
    diffs: List[DiffEntry] = []
    prev_kind = None

    for r in regions:
        if r.kind in ("FENCE_CODE", "INDENT_CODE", "QUOTE"):
            out.append(r.raw)
        elif r.kind == "BLANK":
            line_count = r.raw.count("\n") + (0 if r.raw.endswith("\n") else 1)
            if collect_diffs and line_count >= 2:
                diffs.append(DiffEntry(
                    r.start, "BLANK_COLLAPSE",
                    f"<{line_count} 个空行>", "<1 个空行>",
                    "连续空行折叠为一个"))
            out.append("\n")
        else:
            normalized, pd = normalize_paragraph(r.raw, r.start, collect_diffs)
            diffs.extend(pd)
            out.append(normalized)
        prev_kind = r.kind

    result = "".join(out)
    if not result.endswith("\n"):
        result += "\n"
        if collect_diffs:
            diffs.append(DiffEntry(len(_split_lines_keepends(text)), "FINAL_NEWLINE",
                                   "<无结尾换行>", "<一个 \\n>", "文件末尾补一个换行"))
    while result.endswith("\n\n"):
        result = result[:-1]
    return result, diffs


# ---------------------------------------------------------------- 代码块校验

def code_region_hashes(text: str, normalized: str):
    """对比原文与结果中每个代码块区域的 SHA-256，返回逐块校验记录。"""
    def codes(regions):
        return [(r.start, r.kind, r.raw) for r in regions
                if r.kind in ("FENCE_CODE", "INDENT_CODE")]

    before = codes(parse_regions(text))
    after = codes(parse_regions(normalized))
    records = []
    for (ls, kind, raw1), (_, _, raw2) in zip(before, after):
        h1 = hashlib.sha256(raw1.encode("utf-8")).hexdigest()
        h2 = hashlib.sha256(raw2.encode("utf-8")).hexdigest()
        records.append({
            "start_line": ls, "kind": kind, "bytes": len(raw1.encode("utf-8")),
            "sha256_original": h1, "sha256_normalized": h2,
            "identical": h1 == h2 and raw1 == raw2,
        })
    return records
