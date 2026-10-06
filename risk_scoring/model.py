"""变更风险评分模型。

设计原则
--------
1. 每个因素先做 [0,1] 归一化（1 = 该因素风险最高），再乘权重得到贡献分。
2. 每个变更的总分 = 各因素贡献分之和（0~100），并保留逐项分解。
3. 权重单独放在 ``DEFAULT_WEIGHTS``，可被替换，便于敏感度分析与按仓库校准。

文献依据见 docs/FACTORS_AND_WEIGHTS.md。文献给出的是"哪些因素与缺陷/回滚
相关、相对强弱如何"，并不提供可跨仓库直接复用的系数，因此这里的权重是
"依据文献相对量级归一化 + 可调校准参数"，其稳健性由 sensitivity 模块量化。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Mapping

# ---------------------------------------------------------------------------
# 因素定义
# ---------------------------------------------------------------------------
# 顺序即展示顺序。key 同时是 Change 中计算因素风险度的函数名后缀。
FACTORS = (
    ("size",         "变更规模：增删行数"),
    ("diffusion",    "扩散度：涉及文件/模块数"),
    ("criticality",  "路径关键性：改动历史高发故障区域"),
    ("test_gap",     "测试缺口：改动代码缺乏测试覆盖"),
    ("inexperience", "作者经验不足：对该区域/仓库的经验"),
    ("low_ownership","所有权弱：非主要维护者改动他人代码"),
    ("off_hours",    "非工作时段/周末提交"),
)

FACTOR_NAMES: tuple = tuple(name for name, _ in FACTORS)
FACTOR_DESCRIPTIONS: Dict[str, str] = dict(FACTORS)

# 默认权重（和为 1）。来源与相对量级依据见 docs/FACTORS_AND_WEIGHTS.md。
# 简要对应：
#   size/diffusion  -> Mockus & Weiss (2000), Nagappan & Ball (2005)
#   criticality     -> Graves et al. (2000)（历史故障热点）
#   test_gap        -> DORA Accelerate State of DevOps（测试自动化/质量内建）
#   inexperience    -> Mockus & Weiss (2000), Eyolfson et al. (2011)
#   low_ownership   -> Bird et al. (2011) "Don't Touch My Code!"
#   off_hours       -> Eyolfson, Tan & Lam (MSR 2011)
DEFAULT_WEIGHTS: Dict[str, float] = {
    "size":          0.18,
    "diffusion":     0.17,
    "criticality":   0.17,
    "test_gap":      0.18,
    "inexperience":  0.12,
    "low_ownership": 0.12,
    "off_hours":     0.06,
}

# 风险等级阈值（含边界）。[30, 60) 为临界区间，sensitivity 会检查该区内的结论稳定性。
THRESHOLD_MEDIUM = 30.0
THRESHOLD_HIGH = 60.0


# ---------------------------------------------------------------------------
# 输入与输出结构
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Change:
    """一次变更的原始特征（均为可从 VCS/CI 直接取得的字段）。"""

    churn_lines: int = 0                 # 增删总行数
    files_touched: int = 1               # 涉及文件数
    modules_touched: int = 1             # 涉及模块/顶层目录数
    criticality: float = 0.0             # 改动区域历史故障密度分位 [0,1]
    test_coverage: float = 1.0           # 改动代码测试覆盖率 [0,1]
    author_commits: int = 1000           # 作者在该仓库的历史提交数
    ownership_ratio: float = 1.0         # 作者对改动文件的历史贡献占比 [0,1]
    hour_local: int = 14                 # 提交本地时间小时 [0,23]
    is_weekend: bool = False


@dataclass(frozen=True)
class Contribution:
    factor: str
    description: str
    weight: float
    factor_value: float                  # 归一化后的因素风险度 [0,1]
    points: float                       # 该因素贡献的分数（0~100 制）


@dataclass(frozen=True)
class ScoreBreakdown:
    score: float
    band: str
    contributions: tuple
    is_boundary: bool = False

    def contribution_for(self, factor: str) -> Contribution:
        for c in self.contributions:
            if c.factor == factor:
                return c
        raise KeyError(factor)

    def shares(self) -> Dict[str, float]:
        """各因素贡献占总分的比例（百分比）。"""
        if self.score <= 0:
            return {c.factor: 0.0 for c in self.contributions}
        return {c.factor: c.points / self.score for c in self.contributions}


# ---------------------------------------------------------------------------
# 因素归一化函数：原始特征 -> [0,1] 风险度
# ---------------------------------------------------------------------------
def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def f_size(change: Change) -> float:
    """规模风险：100 行以内近似线性，~800 行达到饱和。

    依据 Nagappan & Ball (ICSE 2005)：相对代码改动量与缺陷密度正相关，
    呈明显边际递减，故用饱和函数而非线性。
    """
    if change.churn_lines <= 0:
        return 0.0
    # x=100 -> 0.25；x=400 -> 0.625；x=800 -> 0.8；x>=2000 -> 1
    return change.churn_lines / (change.churn_lines + 300.0)


def f_diffusion(change: Change) -> float:
    """扩散风险：文件数与模块数各占一半，分别在 20 文件/5 模块附近饱和。

    Mockus & Weiss (2000) 发现横跨多个模块的变更风险显著更高。
    """
    files_part = change.files_touched / (change.files_touched + 8.0)
    modules_part = min(1.0, change.modules_touched / 6.0)
    return 0.6 * files_part + 0.4 * modules_part


def f_criticality(change: Change) -> float:
    """关键性：直接使用历史故障密度分位（Graves et al., TSE 2000：
    过去频繁出故障的区域，未来变更也更易出故障）。"""
    return _clamp01(change.criticality)


def f_test_gap(change: Change) -> float:
    """测试缺口：覆盖率越低风险越高。DORA 报告中质量内建/测试自动化
    与低变更失败率稳定相关。"""
    return 1.0 - _clamp01(change.test_coverage)


def f_inexperience(change: Change) -> float:
    """经验不足：历史提交数越少风险越高，~50 次提交以上趋近饱和低位。

    Mockus & Weiss (2000) 与 Eyolfson et al. (2011) 均报告新手提交缺陷率更高。
    """
    if change.author_commits <= 0:
        return 1.0
    if change.author_commits >= 500:
        return 0.0  # 500+ 次提交视为完全熟悉，经验因素不再贡献风险
    # 1 次 -> 0.97；10 次 -> 0.75；50 次 -> 0.33；500 次 -> 0
    return 25.0 / (change.author_commits + 25.0)


def f_low_ownership(change: Change) -> float:
    """所有权弱：作者对所改文件历史贡献占比越低，风险越高。
    Bird et al. (FSE 2011)：低所有权/他人代码区域缺陷率显著更高。"""
    return 1.0 - _clamp01(change.ownership_ratio)


def f_off_hours(change: Change) -> float:
    """非工作时段：夜间 22:00-06:00 风险最高，周末全天抬升基线。
    Eyolfson, Tan & Lam (MSR 2011)：深夜提交更易引入缺陷。"""
    h = change.hour_local % 24
    night = 1.0 if h >= 22 or h < 6 else (0.5 if h < 9 or h >= 19 else 0.0)
    weekend = 0.4 if change.is_weekend else 0.0
    return min(1.0, night + weekend)


_FACTOR_FUNCS = {
    "size": f_size,
    "diffusion": f_diffusion,
    "criticality": f_criticality,
    "test_gap": f_test_gap,
    "inexperience": f_inexperience,
    "low_ownership": f_low_ownership,
    "off_hours": f_off_hours,
}


# ---------------------------------------------------------------------------
# 评分
# ---------------------------------------------------------------------------
def _band(score: float) -> str:
    if score >= THRESHOLD_HIGH:
        return "high"
    if score >= THRESHOLD_MEDIUM:
        return "medium"
    return "low"


def normalized_weights(weights: Mapping[str, float] | None = None) -> Dict[str, float]:
    """校验并归一化权重（和为 1）。"""
    w = dict(DEFAULT_WEIGHTS if weights is None else weights)
    missing = set(FACTOR_NAMES) - set(w)
    if missing:
        raise ValueError(f"权重缺少因素: {sorted(missing)}")
    total_w = sum(w.values())
    if total_w <= 0:
        raise ValueError("权重之和必须为正")
    return {name: w[name] / total_w for name in FACTOR_NAMES}


def score_from_values(values, weights=None):
    """从归一化因素值直接计算 (分数, 等级)。敏感度分析用它避免重复对象分配。"""
    nw = normalized_weights(weights)
    total = sum(values[name] * nw[name] for name in FACTOR_NAMES) * 100.0
    return total, _band(total)


def factor_values(change: Change) -> Dict[str, float]:
    """返回归一化后的各因素风险度（0~1），便于单独检查。"""
    return {name: _FACTOR_FUNCS[name](change) for name in FACTOR_NAMES}


def score_change(
    change: Change,
    weights: Mapping[str, float] | None = None,
) -> ScoreBreakdown:
    """对单次变更评分，返回总分、等级与逐项贡献分解。"""
    nw = normalized_weights(weights)
    values = factor_values(change)

    contributions = []
    total = 0.0
    for name, desc in FACTORS:
        value = values[name]
        points = value * nw[name] * 100.0
        total += points
        contributions.append(
            Contribution(
                factor=name,
                description=desc,
                weight=nw[name],
                factor_value=value,
                points=points,
            )
        )

    # 临界区间：分数距任一阈值 5 分以内
    boundary = (
        abs(total - THRESHOLD_MEDIUM) <= 5.0
        or abs(total - THRESHOLD_HIGH) <= 5.0
    )
    return ScoreBreakdown(
        score=total,
        band=_band(total),
        contributions=tuple(contributions),
        is_boundary=boundary,
    )


def score_many(changes, weights: Mapping[str, float] | None = None):
    """批量评分（生成器），用于 10 万级数据，避免结果列表常驻内存。"""
    for change in changes:
        yield score_change(change, weights)
