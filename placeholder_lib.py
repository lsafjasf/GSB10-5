"""printf 风格占位符的校验与还原库（仅使用 Python 标准库）。

支持的占位符语法（printf 风格）：
    %s %d %.2f %+08.3f %-10s %(user_name)s %(count)05d ...
    %% 表示字面量百分号，不是占位符。

核心概念：
    签名（signature）：占位符的身份标识，由 命名参数名(可选) + 标志位 +
    宽度 + 精度 + 转换类型 组成，与在字符串中出现的位置无关。
    例如 %(name)+08.3f、%s、%.2f。

校验规则（按多重集合比较，与顺序无关）：
    1. 把原文和译文各自的占位符解析成签名列表，再分别统计为
       “签名 -> 出现次数”的多重集合（Counter）。
    2. 缺失（missing）：签名在原文出现、译文中一次都没有出现。
    3. 多余（extra）：签名在译文出现、原文中一次都没有出现。
    4. 重复次数不一致（count_mismatch）：签名两边都出现，但次数不同。
    5. 三类问题互不重叠，可同时存在；三者皆空则校验通过。

重复占位符的计数方式：
    同一签名每出现一次计一次。例如原文 "%s 和 %s" 中签名 %s 计数为 2，
    译文必须也恰好出现 2 次 %s 才算通过；出现 1 次记为
    count_mismatch(expected=2, actual=1)，出现 3 次记为
    count_mismatch(expected=2, actual=3)。

还原（restore）的取值映射规则：
    1. 位置参数：原文中的位置占位符按出现顺序依次绑定 values 中的值，
       形成 “签名 -> 值队列”（同签名按原文出现顺序入队）。
    2. 命名参数：按参数名从 named_values 字典取值。
    3. 渲染译文时，按译文中的出现顺序逐个占位符到对应签名的值队列里
       取值（同签名先进先出），并套用占位符自带的宽度/精度/标志位格式化。
    4. 译文中出现原文没有的签名，或译文的占位符数量少于原文导致值有
       剩余，都会抛出 PlaceholderMismatchError（建议先 validate 再 restore）。
    5. 宽度/精度为 * 的占位符可以解析和校验，但还原时无法从单个值
       推导，会抛出 ValueError。
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field

_PLACEHOLDER_RE = re.compile(
    r"""
    %                                  # 起始百分号
    (?:\((?P<name>[^()]+)\))?          # 命名参数: %(name)
    (?P<flags>[-+#0 ]*)                # 标志位: - + # 0 空格
    (?P<width>\*|\d+)?                 # 宽度: 数字或 *
    (?:\.(?P<precision>\*|\d+))?       # 精度: .数字 或 .*
    [hlL]?                             # 长度修饰（解析但忽略，不参与签名）
    (?P<type>[diouxXeEfFgGcrsa%])      # 转换类型
    """,
    re.VERBOSE,
)

_LITERAL_PERCENT = "%"


class PlaceholderMismatchError(ValueError):
    """还原时译文与原文的占位符无法一一对应。"""


@dataclass(frozen=True)
class Placeholder:
    """一个解析出的占位符。"""

    name: str | None        # 命名参数名，位置参数为 None
    flags: str              # 标志位，如 "-+0"
    width: str | None       # 宽度，如 "8"、"*"
    precision: str | None   # 精度（不含点号），如 "2"、"*"
    conv_type: str          # 转换类型，如 "s"、"d"、"f"
    raw: str                # 原始文本
    start: int              # 在源串中的起始下标
    end: int                # 在源串中的结束下标（不含）

    @property
    def signature(self) -> str:
        """占位符的身份标识，与位置无关。"""
        sig = "%"
        if self.name is not None:
            sig += f"({self.name})"
        sig += self.flags
        if self.width is not None:
            sig += self.width
        if self.precision is not None:
            sig += "." + self.precision
        sig += self.conv_type
        return sig


def parse_placeholders(text: str) -> list[Placeholder]:
    """按出现顺序解析文本中的全部占位符（%% 字面量除外）。"""
    result = []
    for m in _PLACEHOLDER_RE.finditer(text):
        if m.group("type") == _LITERAL_PERCENT:
            continue  # %% 是字面量百分号
        result.append(
            Placeholder(
                name=m.group("name"),
                flags=m.group("flags") or "",
                width=m.group("width"),
                precision=m.group("precision"),
                conv_type=m.group("type"),
                raw=m.group(0),
                start=m.start(),
                end=m.end(),
            )
        )
    return result


def signature_counter(text: str) -> Counter:
    """统计文本中各占位符签名的出现次数。"""
    return Counter(p.signature for p in parse_placeholders(text))


@dataclass(frozen=True)
class MissingItem:
    signature: str
    expected_count: int     # 原文中出现次数（译文中为 0）


@dataclass(frozen=True)
class ExtraItem:
    signature: str
    actual_count: int       # 译文中出现次数（原文中为 0）


@dataclass(frozen=True)
class CountMismatch:
    signature: str
    expected_count: int     # 原文次数
    actual_count: int       # 译文次数


@dataclass
class ValidationResult:
    """分类校验结果。三类问题互不重叠。"""

    missing: list[MissingItem] = field(default_factory=list)
    extra: list[ExtraItem] = field(default_factory=list)
    count_mismatches: list[CountMismatch] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.missing or self.extra or self.count_mismatches)

    def summary(self) -> str:
        if self.ok:
            return "OK"
        parts = []
        for item in self.missing:
            parts.append(f"缺失 {item.signature} (原文 {item.expected_count} 次, 译文 0 次)")
        for item in self.extra:
            parts.append(f"多余 {item.signature} (原文 0 次, 译文 {item.actual_count} 次)")
        for item in self.count_mismatches:
            parts.append(
                f"次数不一致 {item.signature} "
                f"(原文 {item.expected_count} 次, 译文 {item.actual_count} 次)"
            )
        return "; ".join(parts)


def validate(source: str, target: str) -> ValidationResult:
    """按多重集合校验译文与原文的占位符是否一致（与顺序无关）。"""
    src = signature_counter(source)
    tgt = signature_counter(target)
    result = ValidationResult()
    for sig in sorted(src.keys() | tgt.keys()):
        expected = src.get(sig, 0)
        actual = tgt.get(sig, 0)
        if expected == actual:
            continue
        if actual == 0:
            result.missing.append(MissingItem(sig, expected))
        elif expected == 0:
            result.extra.append(ExtraItem(sig, actual))
        else:
            result.count_mismatches.append(CountMismatch(sig, expected, actual))
    return result


def _rebuild_spec(ph: Placeholder) -> str:
    """把占位符还原成可用于 % 格式化的格式串。"""
    return ph.signature


def _format_value(ph: Placeholder, value) -> str:
    if ph.width == "*" or ph.precision == "*":
        raise ValueError(f"无法还原使用 * 宽度/精度的占位符: {ph.raw}")
    spec = _rebuild_spec(ph)
    if ph.name is not None:
        return spec % {ph.name: value}
    return spec % value


def _bind_source_values(source: str, values, named_values):
    """把原文占位符绑定到具体值，返回 签名 -> 值队列。"""
    pools: dict[str, deque] = defaultdict(deque)
    value_iter = iter(values)
    named_values = named_values or {}
    for ph in parse_placeholders(source):
        if ph.name is not None:
            if ph.name not in named_values:
                raise PlaceholderMismatchError(
                    f"命名参数 {ph.name!r} 缺少取值"
                )
            value = named_values[ph.name]
        else:
            try:
                value = next(value_iter)
            except StopIteration:
                raise PlaceholderMismatchError(
                    f"位置参数不足：原文占位符 {ph.raw} 没有对应取值"
                ) from None
        pools[ph.signature].append(value)
    leftover = list(value_iter)
    if leftover:
        raise PlaceholderMismatchError(
            f"取值数量多于原文位置占位符数量，多出 {len(leftover)} 个"
        )
    return pools


def restore(source: str, target: str, values=(), named_values=None) -> str:
    """用原文绑定的取值渲染译文（占位符顺序可与原文不同）。

    values: 位置参数取值，按原文中位置占位符的出现顺序对应。
    named_values: 命名参数取值字典。
    """
    pools = _bind_source_values(source, values, named_values)

    def _sub(m: re.Match) -> str:
        if m.group("type") == _LITERAL_PERCENT:
            return _LITERAL_PERCENT
        ph = Placeholder(
            name=m.group("name"),
            flags=m.group("flags") or "",
            width=m.group("width"),
            precision=m.group("precision"),
            conv_type=m.group("type"),
            raw=m.group(0),
            start=m.start(),
            end=m.end(),
        )
        queue = pools.get(ph.signature)
        if not queue:
            raise PlaceholderMismatchError(
                f"译文包含原文没有的占位符 {ph.raw} (签名 {ph.signature})"
            )
        return _format_value(ph, queue.popleft())

    rendered = _PLACEHOLDER_RE.sub(_sub, target)
    leftover = {sig: len(q) for sig, q in pools.items() if q}
    if leftover:
        raise PlaceholderMismatchError(
            f"译文缺少占位符，以下签名未使用: {leftover}"
        )
    return rendered
