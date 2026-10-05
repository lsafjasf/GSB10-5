"""令牌作用域（scope）校验库 —— 仅依赖 Python 3 标准库。

语法（EBNF）
------------
    scope    := segment ( "." segment )*
    segment  := "*" | LITERAL
    LITERAL  := [a-z][a-z0-9_-]{0,62}        # 长度 1..63，区分大小写

说明：
    * 分隔符固定为 "."；"*" 只能作为完整的一段，不允许 "read*"、"**" 这类写法。
    * 段与段之间做的是 *段级* 判定，绝不做字符串前缀判定。

包含关系 covers(G, R)（G=已授权作用域，R=本次请求作用域）
--------------------------------------------------------
    G 覆盖 R，当且仅当同时满足：
      1. len(segments(G)) <= len(segments(R))
         —— G 是 R 的段级前缀，即“上级作用域覆盖其全部下级”；
      2. 对 G 的每一段 g_i 与 R 同位置段 r_i：g_i == r_i 或 g_i == "*"
         —— "*" 精确匹配 *任意一个* 段，且不跨段。
    空作用域、结构非法的作用域一律拒绝（fail-closed），由调用方读取拒绝原因。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Tuple

__all__ = ["ScopeError", "parse_scope", "is_valid_scope", "covers", "check"]

SEP = "."
WILDCARD = "*"
MAX_SEGMENT_LEN = 63

# 每个段只能是小写字母开头的小写/数字/下划线/连字符序列，长度 1..63。
_LITERAL_RE = re.compile(r"[a-z][a-z0-9_-]{0,%d}\Z" % (MAX_SEGMENT_LEN - 1))


class ScopeError(ValueError):
    """作用域为空或结构非法。"""


def parse_scope(scope: object) -> Tuple[str, ...]:
    """把作用域字符串解析为段元组；非法时抛 ScopeError（带原因）。"""
    if not isinstance(scope, str):
        raise ScopeError("作用域必须是字符串，实际类型: %r" % type(scope).__name__)
    if scope == "":
        raise ScopeError("作用域为空字符串")
    if SEP not in scope and scope != WILDCARD and not _LITERAL_RE.match(scope):
        # 单段情况下给出更具体的原因
        raise ScopeError(_illegal_segment_reason(scope))
    segments = scope.split(SEP)
    if len(segments) > 256:
        raise ScopeError("作用域段数 %d 超过上限 256" % len(segments))
    for index, segment in enumerate(segments):
        if segment == WILDCARD:
            continue
        if segment == "":
            raise ScopeError(
                "第 %d 段为空（存在前导点、尾点或连续点）: %r" % (index + 1, scope)
            )
        if len(segment) > MAX_SEGMENT_LEN:
            raise ScopeError(
                "第 %d 段长度 %d 超过上限 %d" % (index + 1, len(segment), MAX_SEGMENT_LEN)
            )
        if not _LITERAL_RE.match(segment):
            raise ScopeError(
                "第 %d 段 %r 非法：%s"
                % (index + 1, segment, _illegal_segment_reason(segment))
            )
    return tuple(segments)


def _illegal_segment_reason(segment: str) -> str:
    if WILDCARD in segment:
        return "通配符 '*' 只能作为完整的一段，不能与其他字符混用"
    if any(ch.isupper() for ch in segment):
        return "作用域区分大小写，只允许小写字母"
    if segment[0].isdigit():
        return "段必须以小写字母开头，不能以数字开头"
    if any(ch.isspace() for ch in segment):
        return "段中不允许空白字符"
    if not all(ch.isascii() and (ch.isalnum() or ch in "_-") for ch in segment):
        return "段只允许 [a-z0-9_-] 字符"
    return "段不满足 [a-z][a-z0-9_-]{0,62}"


def is_valid_scope(scope: object) -> bool:
    try:
        parse_scope(scope)
    except ScopeError:
        return False
    return True


def covers(grant: object, request: object) -> bool:
    """已授权作用域 grant 是否覆盖请求作用域 request；任一非法即返回 False。"""
    try:
        grant_segments = parse_scope(grant)
        request_segments = parse_scope(request)
    except ScopeError:
        return False
    return _segment_covers(grant_segments, request_segments)


def _segment_covers(grant_segments: Tuple[str, ...], request_segments: Tuple[str, ...]) -> bool:
    if len(grant_segments) > len(request_segments):
        # 子级授权不能反过来覆盖上级（files.read 不覆盖 files）
        return False
    for grant_seg, request_seg in zip(grant_segments, request_segments):
        if grant_seg == WILDCARD:
            # "*" 匹配任意一个段；请求方同为 "*" 时按相等处理
            continue
        if grant_seg != request_seg:
            return False
    return True


@dataclass(frozen=True)
class CheckResult:
    allowed: bool
    reason: str  # 允许时为 "ok"，拒绝时为具体原因

    def __bool__(self) -> bool:
        return self.allowed


def check(grant: object, request: object) -> CheckResult:
    """带原因的校验入口；授权方/请求方任一非法都拒绝（fail-closed）。"""
    try:
        grant_segments = parse_scope(grant)
    except ScopeError as exc:
        return CheckResult(False, "授权作用域非法: %s" % exc)
    try:
        request_segments = parse_scope(request)
    except ScopeError as exc:
        return CheckResult(False, "请求作用域非法: %s" % exc)
    if _segment_covers(grant_segments, request_segments):
        return CheckResult(True, "ok")
    return CheckResult(
        False,
        "授权作用域 %r 不包含请求作用域 %r（段级比较不匹配或层级不足）" % (grant, request),
    )
