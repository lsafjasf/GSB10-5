"""令牌作用域校验库（Python 3，仅标准库）。

作用域语法（ABNF）：

    scope    = resource ":" action
    resource = segment *("." segment)
    segment  = literal / "*" / "**"
    literal  = (ALPHA / DIGIT) *(ALPHA / DIGIT / "-" / "_")   ; 仅小写
    action   = "read" / "write" / "readwrite" / "*"

包含（覆盖）规则，逐项判定，不做任何字符串前缀比较：

  1. resource 按 "." 切分为段序列，逐段比较：
       - 字面段：仅当两段完全相等时匹配；
       - "*"    ：匹配且仅匹配请求中的一个段（不可跨段、不可匹配零段）；
       - "**"   ：仅允许作为 resource 的最后一个段，匹配请求中剩余
                  的零个或多个段；
       - 段数必须恰好对齐（"**" 除外），多一段少一段都不匹配。
  2. action 按蕴含闭包判定（读是读写的真子集）：
       read      蕴含 {read}
       write     蕴含 {write}
       readwrite 蕴含 {read, write}
       *         蕴含 {read, write}
     授权 action 的闭包必须包含请求 action。
  3. 被校验（请求）作用域必须是具体作用域，不得含 "*" / "**"；
     通配符只允许出现在令牌持有的授权作用域中。

失败即拒绝（fail closed）：空作用域、结构非法、非法授权项均不放行。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

_LITERAL_RE = re.compile(r"[a-z0-9](?:[a-z0-9_-]*)\Z")

_ACTION_CLOSURE = {
    "read": frozenset({"read"}),
    "write": frozenset({"write"}),
    "readwrite": frozenset({"read", "write"}),
    "*": frozenset({"read", "write"}),
}

ACTIONS = tuple(_ACTION_CLOSURE)


class ScopeError(ValueError):
    """作用域为空或结构非法。"""


@dataclass(frozen=True)
class Scope:
    """解析后的作用域：resource 段序列 + action。"""

    resource: Tuple[str, ...]
    action: str

    @property
    def is_concrete(self) -> bool:
        return all(seg not in ("*", "**") for seg in self.resource)

    def __str__(self) -> str:
        return ".".join(self.resource) + ":" + self.action


def parse_scope(text: str, allow_wildcard: bool = True) -> Scope:
    """解析并校验作用域字符串，非法时抛出 ScopeError 并说明原因。"""
    if text is None:
        raise ScopeError("作用域为 None")
    if not isinstance(text, str):
        raise ScopeError(f"作用域必须是字符串，得到 {type(text).__name__}")
    if text == "":
        raise ScopeError("作用域为空字符串")
    if text != text.strip():
        raise ScopeError("作用域含首尾空白")
    parts = text.split(":")
    if len(parts) != 2:
        raise ScopeError("作用域必须形如 resource:action，且只能含一个 ':'")
    res_text, action = parts
    if not res_text:
        raise ScopeError("resource 部分为空")
    if action not in _ACTION_CLOSURE:
        raise ScopeError(
            f"非法 action {action!r}（允许值：read / write / readwrite / *）"
        )
    segments = tuple(res_text.split("."))
    for index, seg in enumerate(segments):
        if seg == "":
            raise ScopeError("resource 含空段（如 '..' 或首尾 '.'）")
        if seg in ("*", "**"):
            if not allow_wildcard:
                raise ScopeError("请求作用域必须是具体作用域，不得含通配符")
            if seg == "**" and index != len(segments) - 1:
                raise ScopeError("'**' 只能作为 resource 的最后一个段")
        elif not _LITERAL_RE.match(seg):
            raise ScopeError(
                f"非法段 {seg!r}（仅允许小写字母/数字开头，后接小写字母/数字/-/_）"
            )
    return Scope(segments, action)


def _resource_covers(grant_res: Tuple[str, ...], req_res: Tuple[str, ...]) -> bool:
    """逐段判定授权 resource 模式是否覆盖具体请求 resource。"""
    gi = 0
    ri = 0
    while gi < len(grant_res):
        gseg = grant_res[gi]
        if gseg == "**":
            # 解析时已保证 "**" 必为末段：匹配剩余零个或多个段
            return True
        if ri >= len(req_res):
            return False
        if gseg == "*":
            pass  # 匹配恰好一个段
        elif gseg != req_res[ri]:
            return False
        gi += 1
        ri += 1
    return ri == len(req_res)


def covers(grant: Scope, request: Scope) -> bool:
    """判定授权作用域 grant 是否覆盖具体请求作用域 request。"""
    if not request.is_concrete:
        raise ScopeError("被校验作用域必须是具体作用域，不得含通配符")
    return request.action in _ACTION_CLOSURE[grant.action] and _resource_covers(
        grant.resource, request.resource
    )


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    matched_grant: Optional[str] = None


def check(granted_scopes: Iterable[str], requested_scope: str) -> Decision:
    """用令牌持有的一组授权作用域校验一个具体请求作用域。"""
    try:
        request = parse_scope(requested_scope, allow_wildcard=False)
    except ScopeError as exc:
        return Decision(False, f"请求作用域非法：{exc}")
    grants = list(granted_scopes)
    if not grants:
        return Decision(False, "令牌不含任何作用域（空授权），拒绝")
    saw_valid_grant = False
    for raw in grants:
        try:
            grant = parse_scope(raw)
        except ScopeError:
            continue  # 非法授权项永不放行（fail closed）
        saw_valid_grant = True
        if covers(grant, request):
            return Decision(True, "命中授权项", matched_grant=raw)
    if not saw_valid_grant:
        return Decision(False, "令牌作用域全部非法，无可用授权项，拒绝")
    return Decision(False, "无任何授权项覆盖该请求，拒绝")
