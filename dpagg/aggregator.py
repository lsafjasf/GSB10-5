"""隐私聚合器：在共享预算账本上回答带噪聚合查询。

工作流程（每次 ask）：
1. 计算切片指纹（查询类型 + 归一化谓词的 SHA-256）；
2. 完全相同的切片命中缓存 -> 免费返回首次的加噪答案（0 消耗）；
3. 相似切片（谓词条件集合的重叠系数 >= 阈值）-> 标记 similar_to，
   仍按新查询全额扣费（相似不等于免费，防止旁路）；
4. 预算检查：不足则拒绝（BudgetExhaustedError），不回答；
5. 扣费 -> 计算真实值 -> 加噪 -> 记录 -> 返回。
"""

import hashlib
import random

from .ledger import BudgetLedger, BudgetExhaustedError
from .noise import laplace_scale, gaussian_sigma, sample_laplace, sample_gaussian
from . import sensitivity as sens

SIMILARITY_THRESHOLD = 0.6


def _norm_token(value) -> str:
    return " ".join(str(value).strip().lower().split())


def slice_fingerprint(query_type: str, predicate: dict) -> str:
    """切片指纹：查询类型 + 归一化谓词的稳定哈希。"""
    canonical = "|".join(
        f"{k}={_norm_token(predicate[k])}" for k in sorted(predicate)
    )
    raw = f"{query_type}::{canonical}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _predicate_tokens(predicate: dict) -> set:
    """谓词的 key=value 条件集合（归一化后）。"""
    return {f"{_norm_token(k)}={_norm_token(v)}" for k, v in predicate.items()}


def _slice_overlap(set_a: set, set_b: set) -> float:
    """重叠系数 |A∩B| / min(|A|, |B|)。

    语义：较小谓词的条件有多少比例被较大谓词覆盖。子集切片
    （如 {dept=eng} 与 {dept=eng, city=sh}）重叠系数为 1.0，
    能正确识别"查询的是同一批人的子/超集切片"。
    """
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / min(len(set_a), len(set_b))


class PrivateAggregator:
    """带差分隐私的聚合查询入口。

    参数
    ----
    ledger    : 共享预算账本（多个聚合器可共用同一个，实现跨轮共享预算）
    mechanism : "laplace"（纯 epsilon-DP）或 "gaussian"（(epsilon, delta)-DP）
    delta     : 高斯机制每轮消耗的 delta（laplace 机制忽略）
    seed      : 随机种子（测试可复现；生产环境应省略）
    """

    def __init__(self, ledger: BudgetLedger, mechanism: str = "laplace",
                 delta: float = 0.0, seed: int = None):
        if mechanism not in ("laplace", "gaussian"):
            raise ValueError("mechanism 必须是 'laplace' 或 'gaussian'")
        if mechanism == "gaussian" and delta <= 0:
            raise ValueError("gaussian 机制要求 delta > 0")
        self.ledger = ledger
        self.mechanism = mechanism
        self.delta = float(delta)
        self._rng = random.Random(seed)
        self._cache = {}          # fingerprint -> 首次加噪答案
        self._slice_tokens = {}   # fingerprint -> 谓词 token 集合
        self._token_index = {}    # token -> set(fingerprint)，倒排索引

    # ---------- 切片识别 ----------

    def _identify_slice(self, query_type: str, predicate: dict) -> dict:
        fp = slice_fingerprint(query_type, predicate)
        if fp in self._cache:
            return {"fingerprint": fp, "status": "exact", "similar_to": None,
                    "similarity": 1.0}
        tokens = _predicate_tokens(predicate)
        # 倒排索引：只与共享至少一个条件的切片比较，避免全量 O(n) 扫描
        candidates = set()
        for token in tokens:
            candidates.update(self._token_index.get(token, ()))
        best_fp, best_sim = None, 0.0
        for old_fp in candidates:
            sim = _slice_overlap(tokens, self._slice_tokens[old_fp])
            if sim > best_sim:
                best_fp, best_sim = old_fp, sim
        status = "new"
        similar_to = None
        if best_fp is not None and best_sim >= SIMILARITY_THRESHOLD:
            status = "similar"
            similar_to = best_fp
        return {"fingerprint": fp, "status": status,
                "similar_to": similar_to, "similarity": best_sim}

    # ---------- 真实值计算 ----------

    @staticmethod
    def _filter(rows: list, predicate: dict) -> list:
        def match(row):
            for key, value in predicate.items():
                if key not in row:
                    return False
                if str(row[key]).strip().lower() != str(value).strip().lower():
                    return False
            return True
        return [row for row in rows if match(row)]

    def _true_value(self, query_type: str, rows: list, predicate: dict,
                    value_field, lo, hi, buckets):
        selected = self._filter(rows, predicate)
        if query_type == "count":
            return float(len(selected))
        if query_type == "sum":
            return float(sum(min(max(float(r[value_field]), lo), hi)
                             for r in selected))
        if query_type == "mean":
            if not selected:
                return 0.0
            clipped = [min(max(float(r[value_field]), lo), hi) for r in selected]
            return sum(clipped) / len(clipped)
        if query_type == "histogram":
            counts = {str(b): 0.0 for b in buckets}
            for row in selected:
                key = str(row[value_field])
                if key in counts:
                    counts[key] += 1.0
            return counts
        raise ValueError(f"未知查询类型: {query_type}")

    # ---------- 敏感度与噪声 ----------

    def _plan(self, query_type: str, epsilon: float, lo, hi, buckets) -> list:
        """返回 [(子查询名, 敏感度, 噪声参数, epsilon_i, delta_i), ...]"""
        if query_type == "count":
            s = sens.count_sensitivity()
            return [("count", s, self._noise_param(s, epsilon), epsilon,
                     self._delta_for(epsilon))]
        if query_type == "sum":
            s = sens.sum_sensitivity(lo, hi)
            return [("sum", s, self._noise_param(s, epsilon), epsilon,
                     self._delta_for(epsilon))]
        if query_type == "mean":
            s_sum, s_cnt = sens.mean_sensitivity(lo, hi)
            half = epsilon / 2.0
            return [("mean.sum", s_sum, self._noise_param(s_sum, half), half,
                     self._delta_for(half)),
                    ("mean.count", s_cnt, self._noise_param(s_cnt, half), half,
                     self._delta_for(half))]
        if query_type == "histogram":
            s = sens.histogram_sensitivity()
            return [("histogram", s, self._noise_param(s, epsilon), epsilon,
                     self._delta_for(epsilon))]
        raise ValueError(f"未知查询类型: {query_type}")

    def _delta_for(self, epsilon_part: float) -> float:
        # 高斯机制下每个子查询消耗一份 delta（mean 有两个子查询，共 2*delta）
        if self.mechanism != "gaussian":
            return 0.0
        return self.delta

    def _noise_param(self, sensitivity: float, epsilon: float) -> float:
        if self.mechanism == "laplace":
            return laplace_scale(sensitivity, epsilon)
        return gaussian_sigma(sensitivity, epsilon, self.delta)

    def _sample(self, param: float) -> float:
        if self.mechanism == "laplace":
            return sample_laplace(param, self._rng)
        return sample_gaussian(param, self._rng)

    # ---------- 主入口 ----------

    def ask(self, query_type: str, rows: list, predicate: dict = None,
            epsilon: float = 0.1, value_field: str = None,
            lo: float = None, hi: float = None, buckets: list = None) -> dict:
        """回答一次带噪聚合查询。预算不足时抛出 BudgetExhaustedError。"""
        predicate = predicate or {}
        if epsilon <= 0:
            raise ValueError("单次 epsilon 必须为正")
        if query_type in ("sum", "mean") and (lo is None or hi is None):
            raise ValueError(f"{query_type} 查询必须提供值域边界 lo/hi")
        if query_type == "histogram" and not buckets:
            raise ValueError("histogram 查询必须提供 buckets")

        slice_info = self._identify_slice(query_type, predicate)
        fp = slice_info["fingerprint"]

        # 1) 完全重复：免费返回缓存答案
        if slice_info["status"] == "exact":
            record = self.ledger.record_free(
                note=f"重复切片命中缓存 fp={fp}，0 消耗")
            return {"status": "answered", "slice": slice_info,
                    "answer": self._cache[fp], "cached": True,
                    "charged": {"epsilon": 0.0, "delta": 0.0},
                    "noise": [], "record": record}

        # 2) 预算计划与检查（先检查后扣费，不足即拒绝）
        plan = self._plan(query_type, epsilon, lo, hi, buckets)
        eps_total = sum(p[3] for p in plan)
        delta_total = sum(p[4] for p in plan)
        if not self.ledger.can_afford(eps_total, delta_total):
            raise BudgetExhaustedError(eps_total, self.ledger.epsilon_remaining)

        # 3) 扣费
        note = f"{query_type} fp={fp}"
        if slice_info["status"] == "similar":
            note += (f"（与切片 {slice_info['similar_to']} 相似，"
                     f"重叠系数={slice_info['similarity']:.2f}，仍全额扣费）")
        record = self.ledger.spend(eps_total, delta_total, note=note)

        # 4) 计算真实值并加噪
        true_value = self._true_value(query_type, rows, predicate,
                                      value_field, lo, hi, buckets)
        noise_report = []
        if query_type == "mean":
            s_sum, s_cnt = sens.mean_sensitivity(lo, hi)
            clipped = [min(max(float(r[value_field]), lo), hi)
                       for r in self._filter(rows, predicate)]
            true_sum = sum(clipped)
            true_cnt = float(len(clipped))
            noisy_sum = true_sum + self._sample(plan[0][2])
            noisy_cnt = true_cnt + self._sample(plan[1][2])
            answer = noisy_sum / noisy_cnt if noisy_cnt > 1e-9 else 0.0
            noise_report = [
                {"part": "mean.sum", "sensitivity": s_sum,
                 "mechanism": self.mechanism, "noise_param": plan[0][2],
                 "epsilon": plan[0][3], "delta": plan[0][4]},
                {"part": "mean.count", "sensitivity": s_cnt,
                 "mechanism": self.mechanism, "noise_param": plan[1][2],
                 "epsilon": plan[1][3], "delta": plan[1][4]},
            ]
        elif query_type == "histogram":
            param = plan[0][2]
            answer = {k: v + self._sample(param) for k, v in true_value.items()}
            noise_report = [
                {"part": "histogram", "sensitivity": plan[0][1],
                 "mechanism": self.mechanism, "noise_param": param,
                 "epsilon": plan[0][3], "delta": plan[0][4],
                 "buckets": len(buckets)},
            ]
        else:
            param = plan[0][2]
            answer = true_value + self._sample(param)
            noise_report = [
                {"part": query_type, "sensitivity": plan[0][1],
                 "mechanism": self.mechanism, "noise_param": param,
                 "epsilon": plan[0][3], "delta": plan[0][4]},
            ]

        # 5) 缓存与登记切片
        self._cache[fp] = answer
        tokens = _predicate_tokens(predicate)
        self._slice_tokens[fp] = tokens
        for token in tokens:
            self._token_index.setdefault(token, set()).add(fp)

        return {"status": "answered", "slice": slice_info, "answer": answer,
                "cached": False,
                "charged": {"epsilon": eps_total, "delta": delta_total},
                "noise": noise_report, "record": record}
