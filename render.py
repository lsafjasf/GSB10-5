"""结构化内容的文本渲染与反向解析（仅使用 Python 标准库）。

支持的结构：dict / list / str / int / float / bool / None 的任意嵌套。

文本格式（每个逻辑行一个节点）：
  - 映射嵌套：  "key":            后跟缩进 +1 层的子块
  - 映射叶子：  "key": <json>
  - 序列嵌套：  -                 后跟缩进 +1 层的子块
  - 序列叶子：  - <json>
  - 空容器：    {}  /  []        （作为普通 JSON 叶子）
  - 标量：      JSON 编码（字符串带引号，特殊字符被转义，保证单行）

折行规则（详见 README.md）：
  1. 宽度按显示宽度计算（East Asian W/F 记 2，组合字符记 0，其余记 1）。
  2. 缩进（每层 INDENT_UNIT 个空格）计入行宽。
  3. 逻辑行超宽时按显示宽度边界强制断开（可断在任意字符之间，
     超长不可断词因此按确定规则被强制断开）；续行 = 相同缩进 + "\\" + 内容。
  4. 宽字符不可分割：放不下时整体移到下一行。
  5. 续行内容直接拼接回原逻辑行，因此渲染可逆。
"""

from __future__ import annotations

import json
import unicodedata

INDENT_UNIT = 2   # 每层缩进的空格数
CONT_MARK = "\\"  # 续行标记：出现在缩进之后、续行内容之前

__all__ = ["render", "parse", "display_width", "char_width", "INDENT_UNIT", "CONT_MARK"]


# ---------------------------------------------------------------- 宽度计算

def char_width(ch: str) -> int:
    """单个字符的显示宽度。"""
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def display_width(s: str) -> int:
    """字符串的显示宽度（不是字符数）。"""
    return sum(char_width(c) for c in s)


# ---------------------------------------------------------------- 折行

def _split_chunks(payload: str, first_cap: int, cont_cap: int) -> list[str]:
    """把 payload 按显示宽度切成若干块：首块容量 first_cap，其余 cont_cap。

    确定规则：贪心填充；宽字符放不下时整体移到下一块；
    单字符宽度超过容量时抛 ValueError（调用方需保证 width 足够）。
    """
    chunks: list[str] = []
    cap = first_cap
    cur: list[str] = []
    cur_w = 0
    for ch in payload:
        w = char_width(ch)
        if cur and cur_w + w > cap:
            chunks.append("".join(cur))
            cap = cont_cap
            cur = []
            cur_w = 0
        if w > cap:
            raise ValueError(
                f"字符 {ch!r} 显示宽度 {w} 超过行容量 {cap}，请增大 width"
            )
        cur.append(ch)
        cur_w += w
    chunks.append("".join(cur))
    return chunks


def _emit(lines: list[str], indent: int, payload: str, width: int) -> None:
    indent_str = " " * (indent * INDENT_UNIT)
    cap = width - len(indent_str)  # 缩进计入宽度
    if cap < len(CONT_MARK) + 2:
        raise ValueError(
            f"宽度 {width} 不足以容纳缩进 {len(indent_str)} 和续行标记，请增大 width"
        )
    if display_width(payload) <= cap:
        lines.append(indent_str + payload)
        return
    chunks = _split_chunks(payload, cap, cap - len(CONT_MARK))
    lines.append(indent_str + chunks[0])
    for chunk in chunks[1:]:
        lines.append(indent_str + CONT_MARK + chunk)


# ---------------------------------------------------------------- 渲染

def _scalar_json(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def _render_node(node, indent: int, lines: list[str], width: int) -> None:
    if isinstance(node, dict):
        if not node:
            _emit(lines, indent, "{}", width)
            return
        for key, value in node.items():
            kjson = json.dumps(str(key), ensure_ascii=False)
            if isinstance(value, (dict, list)) and value:
                _emit(lines, indent, kjson + ":", width)
                _render_node(value, indent + 1, lines, width)
            else:
                _emit(lines, indent, kjson + ": " + _scalar_json(value), width)
    elif isinstance(node, (list, tuple)):
        if not node:
            _emit(lines, indent, "[]", width)
            return
        for item in node:
            if isinstance(item, (dict, list, tuple)) and item:
                _emit(lines, indent, "-", width)
                _render_node(item, indent + 1, lines, width)
            else:
                _emit(lines, indent, "- " + _scalar_json(item), width)
    else:
        _emit(lines, indent, _scalar_json(node), width)


def render(node, width: int = 40) -> str:
    """把嵌套结构渲染为文本，每个物理行显示宽度 <= width（缩进计入）。"""
    if width < 4:
        raise ValueError("width 至少为 4")
    lines: list[str] = []
    _render_node(node, 0, lines, width)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 解析

_decoder = json.JSONDecoder()


def _try_key(payload: str):
    """若 payload 形如 <json-string>: ... 则返回 (key, rest|None)，否则 None。"""
    if not payload.startswith('"'):
        return None
    try:
        key, end = _decoder.raw_decode(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(key, str):
        return None
    rest = payload[end:]
    if rest == ":":
        return key, None
    if rest.startswith(": "):
        return key, rest[2:]
    return None


def _parse_block(logical: list[list], i: int, indent: int):
    if i >= len(logical):
        raise ValueError("缺少子块")
    if logical[i][0] != indent:
        raise ValueError(f"第 {i} 个逻辑行缩进 {logical[i][0]}，期望 {indent}")
    payload = logical[i][1]
    if payload == "-" or payload.startswith("- "):
        return _parse_list(logical, i, indent)
    if _try_key(payload) is not None:
        return _parse_dict(logical, i, indent)
    return json.loads(payload), i + 1


def _parse_dict(logical: list[list], i: int, indent: int):
    out = {}
    while i < len(logical) and logical[i][0] == indent:
        kv = _try_key(logical[i][1])
        if kv is None:
            break
        key, rest = kv
        if rest is None:
            child, i = _parse_block(logical, i + 1, indent + 1)
            out[key] = child
        else:
            out[key] = json.loads(rest)
            i += 1
    return out, i


def _parse_list(logical: list[list], i: int, indent: int):
    out = []
    while i < len(logical) and logical[i][0] == indent:
        payload = logical[i][1]
        if payload == "-":
            child, i = _parse_block(logical, i + 1, indent + 1)
            out.append(child)
        elif payload.startswith("- "):
            out.append(json.loads(payload[2:]))
            i += 1
        else:
            break
    return out, i


def parse(text: str):
    """把 render 产出的文本解析回原始结构。"""
    logical: list[list] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        if raw == "":
            continue
        n_spaces = len(raw) - len(raw.lstrip(" "))
        if n_spaces % INDENT_UNIT:
            raise ValueError(f"第 {lineno} 行缩进不是 {INDENT_UNIT} 的整数倍")
        indent = n_spaces // INDENT_UNIT
        payload = raw[n_spaces:]
        if payload.startswith(CONT_MARK):
            if not logical:
                raise ValueError(f"第 {lineno} 行续行没有前驱行")
            logical[-1][1] += payload[len(CONT_MARK):]
        else:
            logical.append([indent, payload])
    if not logical:
        raise ValueError("空文本")
    node, next_i = _parse_block(logical, 0, logical[0][0])
    if next_i != len(logical):
        raise ValueError(f"第 {next_i} 个逻辑行起无法解析")
    return node
