"""口令策略校验库（仅标准库）。

长度口径说明
------------
本库的长度一律按 **Unicode 码点数（字符数）** 统计，即 Python 的 ``len(str)``，
**不是字节数**。例如：

- ``"abc"`` -> 3 个字符
- ``"口令ab"`` -> 4 个字符（UTF-8 下是 10 字节，但按 4 计）
- ``"é"``（U+00E9，单码点）-> 1 个字符
- ``"e"``（U+0065 + U+0301 组合序列）-> 2 个字符（组合符单独计数）

判定结果中会同时附上 UTF-8 字节数，便于需要字节口径的调用方自行换算。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple


@dataclass(frozen=True)
class RuleResult:
    """单条策略的判定明细。"""

    rule_id: str
    passed: bool
    message: str  # 面向用户的可读说明（未通过时是失败原因，通过时是确认语）


@dataclass(frozen=True)
class ValidationResult:
    """整体验证结果：逐条明细 + 全部违规项（按策略定义的可读顺序排列）。"""

    ok: bool
    results: Tuple[RuleResult, ...]
    char_length: int  # 码点数（字符数）
    byte_length: int  # UTF-8 字节数，仅供换算参考

    @property
    def violations(self) -> Tuple[RuleResult, ...]:
        return tuple(r for r in self.results if not r.passed)

    def explain(self) -> str:
        lines = []
        for r in self.results:
            mark = "PASS" if r.passed else "FAIL"
            lines.append(f"[{mark}] {r.rule_id}: {r.message}")
        summary = "通过" if self.ok else f"未通过（{len(self.violations)} 条违规）"
        lines.append(
            f"结论: {summary}；长度 {self.char_length} 字符 / {self.byte_length} 字节(UTF-8)"
        )
        return "\n".join(lines)


def _rule_min_length(pw: str, p: "PasswordPolicy") -> Tuple[bool, str]:
    n = len(pw)
    if n >= p.min_length:
        return True, f"长度 {n} 字符，满足最小长度 {p.min_length}"
    return False, f"长度 {n} 字符，小于最小长度 {p.min_length}（按字符数计）"


def _rule_max_length(pw: str, p: "PasswordPolicy") -> Tuple[bool, str]:
    n = len(pw)
    if n <= p.max_length:
        return True, f"长度 {n} 字符，未超过最大长度 {p.max_length}"
    return False, f"长度 {n} 字符，超过最大长度 {p.max_length}（按字符数计）"


def _rule_require_lower(pw: str, p: "PasswordPolicy") -> Tuple[bool, str]:
    if any(c.islower() for c in pw):
        return True, "包含小写字母"
    return False, "缺少小写字母（a-z）"


def _rule_require_upper(pw: str, p: "PasswordPolicy") -> Tuple[bool, str]:
    if any(c.isupper() for c in pw):
        return True, "包含大写字母"
    return False, "缺少大写字母（A-Z）"


def _rule_require_digit(pw: str, p: "PasswordPolicy") -> Tuple[bool, str]:
    if any(c.isdigit() for c in pw):
        return True, "包含数字"
    return False, "缺少数字（0-9）"


def _rule_require_symbol(pw: str, p: "PasswordPolicy") -> Tuple[bool, str]:
    if any(c in p.symbols for c in pw):
        return True, "包含符号"
    return False, f"缺少符号（可用：{p.symbols}）"


def _rule_no_whitespace(pw: str, p: "PasswordPolicy") -> Tuple[bool, str]:
    if not any(c.isspace() for c in pw):
        return True, "不含空白字符"
    return False, "含有空白字符（空格/制表符/换行等）"


def _rule_max_repeat(pw: str, p: "PasswordPolicy") -> Tuple[bool, str]:
    run = 1
    for i in range(1, len(pw)):
        run = run + 1 if pw[i] == pw[i - 1] else 1
        if run > p.max_repeat:
            return False, f"字符 {pw[i]!r} 连续重复 {run} 次，超过上限 {p.max_repeat}"
    return True, f"无超过 {p.max_repeat} 次的连续重复字符"


def _rule_not_common(pw: str, p: "PasswordPolicy") -> Tuple[bool, str]:
    if pw.lower() not in p.common_passwords:
        return True, "不在常见弱口令名单中"
    return False, "属于常见弱口令，禁止使用"


def _rule_not_contains_username(pw: str, p: "PasswordPolicy") -> Tuple[bool, str]:
    username = p.username
    if not username or username.lower() not in pw.lower():
        return True, "不包含用户名"
    return False, f"包含用户名 {username!r}，禁止使用"


# (rule_id, 启用条件, 判定函数)，顺序即输出顺序（先长度，再组成，再其他）
_RULE_TABLE: Tuple[Tuple[str, Callable[["PasswordPolicy"], bool], Callable], ...] = (
    ("min_length", lambda p: p.min_length is not None, _rule_min_length),
    ("max_length", lambda p: p.max_length is not None, _rule_max_length),
    ("require_lower", lambda p: p.require_lower, _rule_require_lower),
    ("require_upper", lambda p: p.require_upper, _rule_require_upper),
    ("require_digit", lambda p: p.require_digit, _rule_require_digit),
    ("require_symbol", lambda p: p.require_symbol, _rule_require_symbol),
    ("no_whitespace", lambda p: p.no_whitespace, _rule_no_whitespace),
    ("max_repeat", lambda p: p.max_repeat is not None, _rule_max_repeat),
    ("not_common", lambda p: bool(p.common_passwords), _rule_not_common),
    ("not_contains_username", lambda p: bool(p.username), _rule_not_contains_username),
)


@dataclass(frozen=True)
class PasswordPolicy:
    """口令策略。所有启用的条目逐条独立判定，违规全部列出。"""

    min_length: Optional[int] = 8
    max_length: Optional[int] = 64
    require_lower: bool = True
    require_upper: bool = True
    require_digit: bool = True
    require_symbol: bool = False
    symbols: str = "!@#$%^&*()-_=+[]{};:,.<>?/|~"
    no_whitespace: bool = True
    max_repeat: Optional[int] = 3
    common_passwords: frozenset = frozenset()
    username: Optional[str] = None

    def validate(self, password: str) -> ValidationResult:
        if not isinstance(password, str):
            raise TypeError("password 必须是 str")
        results: List[RuleResult] = []
        for rule_id, enabled, check in _RULE_TABLE:
            if not enabled(self):
                continue
            passed, message = check(password, self)
            results.append(RuleResult(rule_id, passed, message))
        return ValidationResult(
            ok=all(r.passed for r in results),
            results=tuple(results),
            char_length=len(password),
            byte_length=len(password.encode("utf-8")),
        )


DEFAULT_COMMON_PASSWORDS = frozenset(
    {
        "password",
        "password1",
        "12345678",
        "123456789",
        "qwerty123",
        "abc12345",
        "11111111",
        "iloveyou",
        "admin123",
        "letmein1",
    }
)
