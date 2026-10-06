"""差分隐私聚合库（Python 3，仅标准库）。

核心组成：
- PrivacyAccountant : 全局唯一的隐私预算账本，纯 ε-DP，基本组合定理。
- DPAnalytics       : 在记录集上执行 count / sum / avg 聚合，按敏感度加 Laplace 噪声。
- LedgerEntry       : 每轮查询的预算消耗记录。
- BudgetExhausted   : 预算不足时抛出，查询不会得到任何结果。

设计要点：
- 多轮查询共享同一个 PrivacyAccountant（同一对象即同一份预算）。
- 噪声尺度 b = Δf / ε，Δf 为查询函数的全局敏感度，ε 为该轮分配到的预算。
- 完全相同的切片（过滤器 + 聚合类型 + 字段一致）命中缓存，零额外预算，
  账本中 reused=True。
- 仅在缓存未命中时做相似切片识别（Jaccard ≥ 阈值），相似不免费，
  照常扣预算，但账本 similar_to 记录识别结果。
"""

from __future__ import annotations

import math
import random
from collections import namedtuple

LedgerEntry = namedtuple(
    "LedgerEntry",
    [
        "round_id",        # 第几轮（从 1 开始）
        "agg",             # 聚合类型 count / sum / avg
        "field",           # sum/avg 的字段（count 为 None）
        "filters",         # 切片过滤器（规范化后的 tuple）
        "epsilon_spent",   # 本轮消耗预算（命中缓存为 0.0）
        "epsilon_remaining",  # 消耗后剩余预算
        "sensitivity",     # 全局敏感度（avg 为 (sum敏感度, count敏感度)）
        "noise_scale",     # Laplace 尺度 b（avg 为两个分量）
        "reused",          # 是否复用了先前完全相同的含噪结果
        "similar_to",      # 相似切片的过滤器，无则为 None
    ],
)


class BudgetExhausted(Exception):
    """剩余隐私预算不足以支付本轮查询。

    抛出该异常时聚合结果与噪声都不会产生，数据方不会得到任何回答，
    账本中也不会增加本轮消耗（拒绝即零消耗）。
    """


class PrivacyAccountant:
    """隐私预算账本（纯 ε-DP / basic composition）。

    规则：
    - 设总预算 ε_total。第 i 轮申请 ε_i，则在 ε_total 内可答的条件是
      Σ_i ε_i ≤ ε_total（基本组合定理，各轮机制各自满足 ε_i-DP，
      组合结果满足 (Σ ε_i)-DP）。
    - 申请必须先经 check，余额不足立即拒绝，不允许“先答后扣”。
    - 浮点比较保留 1e-12 容差，避免末尾预算因舍入误差被误拒。
    """

    def __init__(self, epsilon_total: float):
        if epsilon_total <= 0:
            raise ValueError("epsilon_total 必须为正数")
        self.epsilon_total = float(epsilon_total)
        self.spent = 0.0

    @property
    def remaining(self) -> float:
        return max(0.0, self.epsilon_total - self.spent)

    def check(self, epsilon: float) -> bool:
        if epsilon <= 0:
            raise ValueError("每轮 epsilon 必须为正数")
        return epsilon <= self.remaining + 1e-12

    def spend(self, epsilon: float) -> None:
        """先检查后扣除；不足则抛 BudgetExhausted，账本状态不变。"""
        if not self.check(epsilon):
            raise BudgetExhausted(
                "隐私预算不足：剩余 %.6f，本轮申请 %.6f"
                % (self.remaining, epsilon)
            )
        self.spent += epsilon


def laplace_noise(scale: float, rng: random.Random) -> float:
    """生成 Laplace(0, scale) 噪声（逆 CDF，仅用 random）。"""
    u = rng.random() - 0.5
    return -scale * math.copysign(1.0, u) * math.log1p(-2.0 * abs(u))


class DPAnalytics:
    """带隐私预算与噪声的聚合查询器。

    records: 记录列表，每条记录是 dict。
    epsilon_total: 所有轮次共享的总预算 ε。
    contribution_bounds: 字段的贡献截断上界 C。sum/avg 前数值先截断到 [0, C]，
        单条记录对求和的影响至多为 C，即 Δ(sum)=C；未配置字段默认 C=1。
    similarity_threshold: 相似切片判定阈值，Jaccard(过滤器集合) ≥ 阈值视为相似。
    seed: 随机噪声种子（便于自测复现）。
    """

    def __init__(
        self,
        records: list,
        epsilon_total: float,
        contribution_bounds: dict | None = None,
        similarity_threshold: float = 0.8,
        seed: int | None = None,
    ):
        self.records = records
        self.accountant = PrivacyAccountant(epsilon_total)
        self.bounds = dict(contribution_bounds or {})
        self.similarity_threshold = similarity_threshold
        self._rng = random.Random(seed)
        self._canon_pool: dict = {}     # 规范化过滤器内部化，避免重复切片重复占内存
        self._index: dict = {}          # (字段, 取值) -> 记录下标集合
        self._slice_cache: dict = {}    # 规范化过滤器 -> frozenset 下标
        self._answer_cache: dict = {}   # 查询键 -> 已发布含噪结果
        self._filter_sets: list = []    # 历史切片过滤器集合（用于相似识别）
        self.ledger: list[LedgerEntry] = []

    # ---- 内部工具 -------------------------------------------------------

    def _build_index(self, key: str) -> None:
        posting: dict = {}
        for idx, row in enumerate(self.records):
            posting.setdefault(row.get(key), set()).add(idx)
        self._index[key] = posting

    def _canonical_filters(self, filters: dict) -> tuple:
        return tuple(sorted((k, v) for k, v in filters.items()))

    @staticmethod
    def _query_key(agg: str, field: str | None, canon: tuple):
        return (agg, field, canon)

    def _slice_indices(self, canon: tuple) -> frozenset:
        if canon in self._slice_cache:
            return self._slice_cache[canon]
        if not canon:
            indices = frozenset(range(len(self.records)))
        else:
            sets = []
            for key, value in canon:
                if key not in self._index:
                    self._build_index(key)
                sets.append(self._index[key].get(value, set()))
            sets.sort(key=len)
            indices = frozenset(sets[0]) if sets else frozenset()
            for posting in sets[1:]:
                indices = indices.intersection(posting)
        self._slice_cache[canon] = indices
        return indices

    def _find_similar(self, canon: tuple) -> tuple | None:
        """与历史切片计算 Jaccard；仅在缓存未命中时调用。"""
        current = set(canon)
        for past in self._filter_sets:
            union = current | set(past)
            if not union:
                continue
            score = len(current & set(past)) / len(union)
            if score >= self.similarity_threshold:
                return past
        return None

    def _bound(self, field: str) -> float:
        return float(self.bounds.get(field, 1.0))

    def _clamped_values(self, field: str, indices: frozenset) -> list:
        cap = self._bound(field)
        values = []
        for idx in indices:
            value = float(self.records[idx].get(field, 0.0))
            if value < 0:
                value = 0.0
            elif value > cap:
                value = cap
            values.append(value)
        return values

    # ---- 无噪声真值（仅用于审计 / 自测，不属于隐私发布接口） -------------

    def true_aggregate(self, filters: dict, agg: str = "count",
                       field: str | None = None) -> float:
        canon = self._canonical_filters(filters)
        indices = self._slice_indices(canon)
        if agg == "count":
            return float(len(indices))
        if agg == "sum":
            return float(sum(self._clamped_values(field, indices)))
        if agg == "avg":
            values = self._clamped_values(field, indices)
            return sum(values) / len(values) if values else 0.0
        raise ValueError("未知聚合类型: %s" % agg)

    # ---- 隐私查询接口 ---------------------------------------------------

    def query(self, filters: dict, agg: str = "count", field: str | None = None,
              epsilon: float | None = None) -> float:
        """执行一轮隐私聚合查询，返回含 Laplace 噪声的结果。

        epsilon: 本轮申请预算；None 时使用默认值 0.1。
        完全重复查询直接返回缓存结果（0 预算）；预算不足抛 BudgetExhausted。
        """
        if agg not in ("count", "sum", "avg"):
            raise ValueError("agg 必须是 count / sum / avg")
        if agg in ("sum", "avg") and field is None:
            raise ValueError("%s 查询必须指定 field" % agg)
        if epsilon is None:
            epsilon = 0.1
        epsilon = float(epsilon)

        round_id = len(self.ledger) + 1
        canon = self._canonical_filters(filters)
        canon = self._canon_pool.setdefault(canon, canon)
        key = self._query_key(agg, field, canon)

        # 1) 完全相同的切片：复用已发布结果，零额外预算。
        if key in self._answer_cache:
            entry = LedgerEntry(
                round_id, agg, field, canon, 0.0,
                self.accountant.remaining, None, None, True, None,
            )
            self.ledger.append(entry)
            return self._answer_cache[key]

        # 2) 相似切片识别（不免费，照常扣预算）。
        similar_to = self._find_similar(canon)

        # 3) 先扣预算；不足则拒绝，不产生答案、不产生消耗。
        self.accountant.spend(epsilon)

        indices = self._slice_indices(canon)

        if agg == "count":
            sensitivity = 1.0                       # 增删一条记录，计数至多变 1
            scale = sensitivity / epsilon
            answer = float(len(indices)) + laplace_noise(scale, self._rng)
        elif agg == "sum":
            sensitivity = self._bound(field)        # Δ(sum)=截断上界 C
            scale = sensitivity / epsilon
            true_value = sum(self._clamped_values(field, indices))
            answer = true_value + laplace_noise(scale, self._rng)
        else:  # avg = 含噪 sum / 含噪 count，ε 平均分给两个子机制
            half = epsilon / 2.0
            sens_sum = self._bound(field)
            sens_count = 1.0
            scale_sum = sens_sum / half
            scale_count = sens_count / half
            values = self._clamped_values(field, indices)
            noisy_sum = sum(values) + laplace_noise(scale_sum, self._rng)
            noisy_count = len(indices) + laplace_noise(scale_count, self._rng)
            sensitivity = (sens_sum, sens_count)
            scale = (scale_sum, scale_count)
            if noisy_count <= 0:
                noisy_count = 1e-9
            answer = noisy_sum / noisy_count

        self._answer_cache[key] = answer
        self._filter_sets.append(canon)
        self.ledger.append(
            LedgerEntry(
                round_id, agg, field, canon, epsilon,
                self.accountant.remaining, sensitivity, scale,
                False, similar_to,
            )
        )
        return answer
