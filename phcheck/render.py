"""译文还原：把取值按占位符身份映射进译文（仅标准库）。"""
from __future__ import annotations

from typing import Any, Mapping

from .tokens import Token, scan
from .validate import validate


class RenderError(Exception):
    """取值映射或渲染失败。"""


def restore(source: str, translation: str, values: Any, check: bool = True) -> str:
    """按译文结构渲染最终结果。

    取值映射规则：
    - 命名占位符 %(name)c  → values[name]（values 为 Mapping）
    - 位置占位符 %n$c      → 第 n 个值（序列下标 n-1，或 Mapping 的 n / "n" 键）
    - 裸占位符 %c          → 译文中的第 k 个裸占位符取第 k 个值（与运行时行为一致）

    check=True（默认）时先做多重集校验，不一致直接抛 RenderError。
    """
    if check:
        report = validate(source, translation)
        if not report.ok:
            raise RenderError("占位符校验未通过: {}".format(report.as_dict()))
    src = scan(source)
    if src.invalid:
        first = src.invalid[0]
        raise RenderError("原文含非法占位符 {!r}（{}）".format(first.text, first.reason))
    tgt = scan(translation)
    if tgt.invalid:
        first = tgt.invalid[0]
        raise RenderError("译文含非法占位符 {!r}（{}）".format(first.text, first.reason))
    out = []
    for chunk in tgt.chunks:
        token = chunk.token
        if token is None:
            out.append(chunk.text)
            continue
        value = _lookup(token, values)
        out.append(_render_token(token, value))
    return "".join(out)


def _lookup(token: Token, values: Any) -> Any:
    try:
        if token.style == "named":
            return values[token.key]
        position = token.index if token.style == "positional" else token.ordinal
        if isinstance(values, Mapping):
            if position in values:
                return values[position]
            return values[str(position)]
        return values[position - 1]
    except (KeyError, IndexError, TypeError) as exc:
        raise RenderError("占位符 {} 取不到值: {}".format(token.raw, exc)) from exc


def _render_token(token: Token, value: Any) -> str:
    if token.conversion == "%":
        return "%"
    # 去掉命名键 / 位置编号 / 长度修饰后，单 token 可直接用原生 % 渲染，
    # 因此宽度、精度、标志的行为与 Python 原生格式化完全一致。
    spec = "%" + token.flags + token.width
    if token.precision is not None:
        spec += "." + token.precision
    spec += token.conversion
    try:
        return spec % value
    except (TypeError, ValueError) as exc:
        raise RenderError(
            "占位符 {} 渲染值 {!r} 失败: {}".format(token.raw, value, exc)
        ) from exc
