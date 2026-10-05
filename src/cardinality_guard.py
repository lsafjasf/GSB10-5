# -*- coding: utf-8 -*-
"""指标标签基数控制库（cardinality_guard）。

只依赖 Python 3 标准库。

解决的问题
----------
把订单号(order_id)、会话号(session_id) 这类"取值基本不重复"的东西当标签后,
指标系统的时间序列数 = 各标签取值数的笛卡尔积, 内存与索引时间全部炸在
标签组合上。本库做两件事:

1. 识别(identify): 依据三条信号判定一个标签是否高基数 ——
   (a) 取值数量        distinct(value) 的绝对规模;
   (b) 增长速率        最近窗口内"首次出现的新取值"占比, 判断是否无界增长;
   (c) 区分度          distinct(value) / 序列数, 判断它到底是"维度"还是"标识符"。
2. 归并(reduce): 三种规则 ——
   TRUNCATE          截断: 保留长字符串前缀(适用于带结构前缀的 ID, 如 u0001234);
   NORMALIZE_PATTERN 归一化(模式): 数字/UUID/十六进制/随机段 -> 占位符(适用于路径、pod 名);
   NORMALIZE_BUCKET  归一化(分桶): 稳定哈希到 N 个桶(保留统计分布);
   DROP              丢弃: 纯标识符直接删标签。
   规则只作用于被识别出的标签, 低基数标签原样保留, 因此不会把本该区分的维度合并。
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter, deque
from dataclasses import dataclass, field

# ---- 规则常量 -------------------------------------------------------------

KEEP = "KEEP"
TRUNCATE = "TRUNCATE"
NORMALIZE_PATTERN = "NORMALIZE_PATTERN"
NORMALIZE_BUCKET = "NORMALIZE_BUCKET"
DROP = "DROP"

ALL_RULES = (KEEP, TRUNCATE, NORMALIZE_PATTERN, NORMALIZE_BUCKET, DROP)

# ---- 识别结论标志 ---------------------------------------------------------

F_HIGH_CARD = "HIGH_CARDINALITY"   # 取值数量超阈值
F_UNBOUNDED = "UNBOUNDED_GROWTH"   # 增长速率超阈值(无界增长)
F_IDENTIFIER = "IDENTIFIER"        # 区分度: 纯标识符
F_LOW_DISTINCT = "LOW_DISTINCTIVENESS"  # 区分度: 分组能力弱

_NUM_RE = re.compile(r"^\d+$")
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_HEX_RE = re.compile(r"^(?:0x)?[0-9a-fA-F]{16,}$")
# 形如 x2abc、a9f3q 的随机后缀(含数字的字母数字串), 用于识别 pod 名等
_TOKEN_RE = re.compile(r"^(?=.*\d)[a-z0-9]{5,}$")
_HEX_SEG_RE = re.compile(r"^[0-9a-fA-F]{8,}$")


@dataclass
class Config:
    """识别阈值配置(默认值的理由见 README「识别依据」节)。"""

    max_values: int = 1000
    """信号(a): 标签取值数量超过它 -> HIGH_CARDINALITY。"""

    window: int = 10_000
    """信号(b): 增长速率统计窗口(该标签最近被观测到的次数)。"""

    max_new_rate: float = 0.30
    """信号(b): 窗口内新取值占比超过它 -> UNBOUNDED_GROWTH。
    稳定维度(如 region)长期接近 0; 单调递增的订单号为 1.0。"""

    id_ratio: float = 0.90
    """信号(c): distinct/series >= 它 -> IDENTIFIER(基本一序列一取值)。"""

    group_ratio: float = 0.50
    """信号(c): distinct/series 在 [group_ratio, id_ratio) -> 分组能力弱。"""

    top_share: float = 0.01
    """辅助信号: 最常见取值的占比低于它, 说明取值分布极度分散(没有"热取值")。"""

    truncate_len: int = 6
    """TRUNCATE 保留的前缀长度。"""

    hash_buckets: int = 16
    """NORMALIZE_BUCKET 的桶数。"""

    truncate_gain: float = 10.0
    """截断后取值数至少下降该倍数, 才值得用 TRUNCATE。"""


@dataclass
class LabelReport:
    """单个标签的识别结果。"""

    label: str
    distinct: int
    series: int
    distinct_ratio: float        # 信号(c)
    new_rate: float              # 信号(b)
    top_value_share: float       # 辅助: 最热取值占比
    pattern_share: float         # 取值形态为 ID 的比例(模式归一化可用)
    truncate_to: int             # 截断到 truncate_len 后的取值数
    flags: list = field(default_factory=list)
    rule: str = KEEP

    def flagged(self) -> bool:
        return bool(self.flags)


class _LabelStats:
    """单标签在线统计(精确模式; 大流量请对样本调用, 见 benchmark.py)。"""

    __slots__ = ("values", "series", "_flags", "_window_new", "window")

    def __init__(self, window: int):
        self.values: Counter = Counter()
        self.series = 0
        self.window = window
        self._flags: deque = deque(maxlen=window)
        self._window_new = 0

    def observe(self, value: str) -> None:
        self.series += 1
        is_new = value not in self.values
        self.values[value] += 1
        # deque 满时最老元素即将被挤掉, 先从窗口新值计数里扣除
        if len(self._flags) == self.window:
            self._window_new -= self._flags[0]
        self._flags.append(1 if is_new else 0)
        self._window_new += 1 if is_new else 0

    def new_rate(self) -> float:
        return self._window_new / len(self._flags) if self._flags else 0.0


# ---- 归一化函数 -----------------------------------------------------------

def normalize_pattern(value: str) -> str:
    """模式归一化: 把具体 ID 替换成占位符, 保留结构。

    纯 ID:        "847291037"            -> "#"
    UUID:         "3f25..-.."            -> ":uuid:"
    长十六进制:    "7f8d9c6b5"           -> ":hex:"
    复合结构:      "web-7f8d9c6b5-x2abc" -> "web-:hex:-:token:"
                  "/users/123/orders/456" -> "/users/#/orders/#"
    """
    if _NUM_RE.match(value):
        return "#"
    if _UUID_RE.match(value):
        return ":uuid:"
    if _HEX_RE.match(value):
        return ":hex:"
    composite = "-" in value or "/" in value
    parts = re.split(r"([-/])", value)
    for i, part in enumerate(parts):
        if not part or part in "-/":
            continue
        if _NUM_RE.match(part):
            parts[i] = "#"
        elif _UUID_RE.match(part):
            parts[i] = ":uuid:"
        elif _HEX_RE.match(part) or _HEX_SEG_RE.match(part):
            parts[i] = ":hex:"
        elif composite and _TOKEN_RE.match(part):
            # 随机后缀段(如 pod 名尾段 x2abc)只在复合结构里识别;
            # 裸 "u0000001" 不处理, 交给截断/分桶规则
            parts[i] = ":token:"
    return "".join(parts)


def normalize_bucket(value: str, buckets: int) -> str:
    """稳定哈希分桶: 同一取值永远落同桶, 不同取值均匀分散, 保留分布形态。"""
    digest = hashlib.blake2b(value.encode("utf-8"), digest_size=8).digest()
    return "b%02d" % (int.from_bytes(digest, "little") % buckets)


def truncate(value: str, length: int) -> str:
    """截断: 超长部分用 * 标记, 短于阈值的原样返回(低基数短串不会被误合并)。"""
    return value if len(value) <= length else value[:length] + "*"


def reduce_value(rule: str, value: str, cfg: Config) -> str:
    if rule == TRUNCATE:
        return truncate(value, cfg.truncate_len)
    if rule == NORMALIZE_PATTERN:
        return normalize_pattern(value)
    if rule == NORMALIZE_BUCKET:
        return normalize_bucket(value, cfg.hash_buckets)
    return value


# ---- 主类 -----------------------------------------------------------------

class CardinalityGuard:
    """采集 -> 识别 -> 归并。

    典型用法::

        guard = CardinalityGuard()
        guard.fit(sample_series)          # 用一批样本识别规则
        reduced = [guard.reduce(labels) for labels in stream]  # 全量流式归并
    """

    def __init__(self, config: Config | None = None):
        self.cfg = config or Config()
        self._stats: dict[str, _LabelStats] = {}
        self.rules: dict[str, str] = {}
        self.reports: dict[str, LabelReport] = {}

    # -- 采集 --

    def observe(self, labels: dict) -> None:
        for key, value in labels.items():
            stats = self._stats.get(key)
            if stats is None:
                stats = _LabelStats(self.cfg.window)
                self._stats[key] = stats
            stats.observe(str(value))

    def fit(self, series) -> dict[str, LabelReport]:
        """对样本(可迭代的 label dict)做识别, 并锁定每个标签的归并规则。"""
        for labels in series:
            self.observe(labels)
        return self.finalize()

    def finalize(self) -> dict[str, LabelReport]:
        self.reports = {
            label: self._build_report(label, stats)
            for label, stats in self._stats.items()
        }
        self.rules = {label: report.rule for label, report in self.reports.items()}
        return self.reports

    # -- 识别 --

    def _build_report(self, label: str, stats: _LabelStats) -> LabelReport:
        cfg = self.cfg
        n = stats.series
        distinct = len(stats.values)
        distinct_ratio = distinct / n if n else 0.0
        new_rate = stats.new_rate()
        top_value_share = stats.values.most_common(1)[0][1] / n if n else 0.0

        truncated = {
            truncate(v, cfg.truncate_len) for v in stats.values
            if len(v) > cfg.truncate_len
        }
        truncate_to = len(truncated) + sum(
            1 for v in stats.values if len(v) <= cfg.truncate_len
        )
        pattern_hits = sum(
            cnt for v, cnt in stats.values.items()
            if normalize_pattern(v) != v
        )
        pattern_share = pattern_hits / n if n else 0.0

        flags: list[str] = []
        # 信号(a) 取值数量
        if distinct > cfg.max_values:
            flags.append(F_HIGH_CARD)
        # 信号(b) 增长速率
        if len(stats._flags) >= min(cfg.window, 200) and new_rate > cfg.max_new_rate:
            flags.append(F_UNBOUNDED)
        # 信号(c) 区分度
        if distinct_ratio >= cfg.id_ratio:
            flags.append(F_IDENTIFIER)
        elif distinct_ratio >= cfg.group_ratio:
            flags.append(F_LOW_DISTINCT)

        report = LabelReport(
            label=label, distinct=distinct, series=n,
            distinct_ratio=distinct_ratio, new_rate=new_rate,
            top_value_share=top_value_share, pattern_share=pattern_share,
            truncate_to=truncate_to, flags=flags,
        )
        report.rule = self._choose_rule(report)
        return report

    def _choose_rule(self, r: LabelReport) -> str:
        """规则决策树(README「归并规则」节有完整说明):

        无任何信号               -> KEEP
        纯标识符(区分度≈1)       -> DROP
        ID 形态取值占比过半       -> NORMALIZE_PATTERN(结构保留)
        前缀截断收益足够大       -> TRUNCATE
        其余高基数/无界增长      -> NORMALIZE_BUCKET(分桶保留分布)
        """
        cfg = self.cfg
        if not r.flags:
            return KEEP
        if F_IDENTIFIER in r.flags:
            return DROP
        if r.pattern_share >= 0.5:
            return NORMALIZE_PATTERN
        gain = r.distinct / r.truncate_to if r.truncate_to else 1.0
        if r.distinct > cfg.max_values and gain >= cfg.truncate_gain and r.truncate_to > 1:
            return TRUNCATE
        return NORMALIZE_BUCKET

    # -- 归并 --

    def reduce(self, labels: dict) -> dict:
        """按锁定规则归并一组标签。

        KEEP 标签(含所有低基数维度)原样返回, 从机制上保证不会合并
        本该区分的维度; DROP 标签被删除; 其余标签按规则改写取值。
        """
        out = {}
        for key, value in labels.items():
            rule = self.rules.get(key, KEEP)
            if rule == DROP:
                continue
            value = str(value)
            out[key] = value if rule == KEEP else reduce_value(rule, value, self.cfg)
        return out
