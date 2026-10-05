"""口令策略校验库（仅使用 Python 标准库）。

设计要点
========
长度口径
--------
本库的长度一律按 **Unicode 码点数（字符数）** 计算，即 Python 的 ``len(str)``：

- ``"abc"`` -> 3 个字符
- ``"密码ab"`` -> 4 个字符（UTF-8 编码后是 8 字节，但本库不按字节计）
- 组合序列按码点逐个计数：``"e\\u0301"``（e + 组合重音符）= 2 个字符，
  而预组合形式 ``"\\u00e9"``（é）= 1 个字符。
- 构造 ``PasswordPolicy(normalize=True)`` 时，会先对口令做 NFC 规范化再校验，
  此时 ``"e\\u0301"`` 会被规范化为 ``"\\u00e9"``，按 1 个字符计。

报告中同时给出 ``length_chars``（码点数，判定依据）与
``length_bytes_utf8``（UTF-8 字节数，仅供参考，不参与判定）。

逐条判定
--------
每条策略独立判定、互不影响：即使长度已经不达标，其余条目仍然照常检查，
违反多条时全部列出，按固定的可读顺序排列（长度 -> 字符类别 -> 其他）。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Optional, Tuple

# 内置的常见弱口令小样例（生产环境应替换为完整黑名单 / k-anonymity 服务）
COMMON_PASSWORDS = frozenset({
    "123456", "123456789", "12345678", "password", "password1",
    "qwerty", "qwerty123", "111111", "abc123", "iloveyou",
    "admin123", "letmein", "welcome", "monkey", "dragon",
    "P@ssw0rd", "Passw0rd", "1q2w3e4r", "qazwsx", "666666",
})

_SYMBOLS = set(r"!@#$%^&*()-_=+[]{}|;:'\",.<>/?`~")


@dataclass(frozen=True)
class RuleResult:
    """单条策略的判定结果。"""

    rule_id: str      # 机器可读的稳定标识，如 "min_length"
    title: str        # 人类可读的条目名
    passed: bool      # 是否通过
    message: str      # 判定说明（通过/未通过的原因）


@dataclass(frozen=True)
class ValidationReport:
    """一次校验的完整结果：逐条明细 + 汇总。"""

    length_chars: int                 # 码点数（长度判定口径）
    length_bytes_utf8: int            # UTF-8 字节数（仅参考）
    results: Tuple[RuleResult, ...]   # 按可读顺序排列的逐条结果

    @property
    def ok(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def violations(self) -> Tuple[RuleResult, ...]:
        """全部未通过条目（可能多条，按可读顺序）。"""
        return tuple(r for r in self.results if not r.passed)

    def explain(self) -> str:
        """生成面向用户/工单的多行说明文本。"""
        lines = [
            f"长度: {self.length_chars} 字符"
            f"（UTF-8 {self.length_bytes_utf8} 字节，长度按字符数判定）"
        ]
        for r in self.results:
            mark = "通过" if r.passed else "未通过"
            lines.append(f"[{mark}] {r.title}: {r.message}")
        if self.ok:
            lines.append("结论: 全部策略通过。")
        else:
            lines.append(f"结论: {len(self.violations)} 条策略未通过，请逐条修正。")
        return "\n".join(lines)


@dataclass(frozen=True)
class PasswordPolicy:
    """口令策略配置。所有条目独立判定。"""

    min_length: int = 8
    max_length: int = 64
    require_lower: bool = True
    require_upper: bool = True
    require_digit: bool = True
    require_symbol: bool = True
    allow_whitespace: bool = False
    max_consecutive_repeat: int = 3   # 同一字符最多连续出现次数
    check_common: bool = True
    username: Optional[str] = None    # 若提供，则口令不得包含用户名
    normalize: bool = False           # True 时先 NFC 规范化再校验

    def validate(self, password: str) -> ValidationReport:
        if not isinstance(password, str):
            raise TypeError("password 必须是 str")
        pw = unicodedata.normalize("NFC", password) if self.normalize else password

        length_chars = len(pw)
        length_bytes = len(pw.encode("utf-8"))
        results = []

        # 1) 长度（先报长度，用户最容易理解）
        results.append(RuleResult(
            "min_length", f"最小长度 {self.min_length} 字符",
            length_chars >= self.min_length,
            f"当前 {length_chars} 字符"
            + ("" if length_chars >= self.min_length
               else f"，还差 {self.min_length - length_chars} 字符"),
        ))
        results.append(RuleResult(
            "max_length", f"最大长度 {self.max_length} 字符",
            length_chars <= self.max_length,
            f"当前 {length_chars} 字符"
            + ("" if length_chars <= self.max_length
               else f"，超出 {length_chars - self.max_length} 字符"),
        ))

        # 2) 字符类别
        if self.require_lower:
            ok = any(c.islower() for c in pw)
            results.append(RuleResult(
                "require_lower", "至少 1 个小写字母", ok,
                "包含小写字母" if ok else "缺少小写字母（a-z）",
            ))
        if self.require_upper:
            ok = any(c.isupper() for c in pw)
            results.append(RuleResult(
                "require_upper", "至少 1 个大写字母", ok,
                "包含大写字母" if ok else "缺少大写字母（A-Z）",
            ))
        if self.require_digit:
            ok = any(c.isdigit() for c in pw)
            results.append(RuleResult(
                "require_digit", "至少 1 个数字", ok,
                "包含数字" if ok else "缺少数字（0-9）",
            ))
        if self.require_symbol:
            ok = any(c in _SYMBOLS for c in pw)
            results.append(RuleResult(
                "require_symbol", "至少 1 个符号", ok,
                "包含符号" if ok else "缺少符号，可用 " + "".join(sorted(_SYMBOLS))[:20] + " 等",
            ))

        # 3) 其他约束
        if not self.allow_whitespace:
            ok = not any(c.isspace() for c in pw)
            results.append(RuleResult(
                "no_whitespace", "不得包含空白字符", ok,
                "不含空白字符" if ok else "包含空格/制表符/换行等空白字符",
            ))
        if self.max_consecutive_repeat > 0:
            run = 1
            worst = 1
            for i in range(1, len(pw)):
                run = run + 1 if pw[i] == pw[i - 1] else 1
                worst = max(worst, run)
            ok = worst <= self.max_consecutive_repeat
            results.append(RuleResult(
                "max_repeat", f"同一字符连续不超过 {self.max_consecutive_repeat} 次",
                ok,
                f"最长连续重复 {worst} 次"
                + ("" if ok else f"，超过上限 {self.max_consecutive_repeat} 次"),
            ))
        if self.check_common:
            ok = pw not in COMMON_PASSWORDS and pw.lower() not in COMMON_PASSWORDS
            results.append(RuleResult(
                "not_common", "不得为常见弱口令", ok,
                "不在常见弱口令名单中" if ok else "命中常见弱口令名单，请更换",
            ))
        if self.username:
            ok = self.username.lower() not in pw.lower()
            results.append(RuleResult(
                "no_username", "不得包含用户名", ok,
                "不包含用户名" if ok else f"包含用户名 {self.username!r}",
            ))

        return ValidationReport(length_chars, length_bytes, tuple(results))
