"""隐私预算账本：多轮查询共享同一份预算，逐轮记录消耗。

规则（详见 BUDGET_RULES.md）：
1. 总预算 (epsilon_total, delta_total) 在创建账本时一次性分配；
2. 采用顺序组合（sequential composition）：各轮消耗直接累加，
   累计消耗不得超过总预算；
3. 先扣费后回答：预算不足的查询被拒绝（BudgetExhaustedError），
   不扣费、不回答、不泄露任何信息；
4. 预算一旦耗尽，后续所有新查询一律拒绝；
5. 账本可被多个聚合器实例共享，保证“多轮共享同一份预算”。
"""

import json
import time


class BudgetExhaustedError(Exception):
    """隐私预算耗尽（或单次申请超出剩余预算）时抛出。"""

    def __init__(self, requested, remaining):
        self.requested = requested
        self.remaining = remaining
        super().__init__(
            f"隐私预算不足：申请 epsilon={requested:.6f}，"
            f"剩余 epsilon={remaining:.6f}，查询被拒绝"
        )


class BudgetLedger:
    """共享预算账本。epsilon 为主预算，delta 仅在高斯机制下消耗。"""

    def __init__(self, epsilon_total: float, delta_total: float = 0.0,
                 spill_path: str = None):
        if epsilon_total <= 0:
            raise ValueError("总预算 epsilon_total 必须为正")
        if delta_total < 0:
            raise ValueError("delta_total 不能为负")
        self.epsilon_total = float(epsilon_total)
        self.delta_total = float(delta_total)
        self.epsilon_spent = 0.0
        self.delta_spent = 0.0
        self._records = []  # 每轮一条消耗记录（spill 模式下不落内存）
        self._seq = 0
        self._spill = open(spill_path, "a", encoding="utf-8") if spill_path else None

    @property
    def epsilon_remaining(self) -> float:
        return self.epsilon_total - self.epsilon_spent

    @property
    def delta_remaining(self) -> float:
        return self.delta_total - self.delta_spent

    @property
    def exhausted(self) -> bool:
        return self.epsilon_remaining <= 0.0

    def can_afford(self, epsilon: float, delta: float = 0.0) -> bool:
        # 容差用于吸收多轮累加的浮点误差（约 1e-9 相对量级），
        # 不改变预算规则的语义：超出真实剩余预算的申请一律拒绝。
        eps_tol = 1e-12 + 1e-9 * self.epsilon_total
        delta_tol = 1e-12 + 1e-9 * max(self.delta_total, 1e-300)
        return (epsilon <= self.epsilon_remaining + eps_tol
                and delta <= self.delta_remaining + delta_tol)

    def spend(self, epsilon: float, delta: float = 0.0, note: str = "") -> dict:
        """扣减预算并追加一条消耗记录；不足则拒绝（不扣费）。"""
        if epsilon < 0 or delta < 0:
            raise ValueError("消耗量不能为负")
        if not self.can_afford(epsilon, delta):
            raise BudgetExhaustedError(epsilon, self.epsilon_remaining)
        self.epsilon_spent += epsilon
        self.delta_spent += delta
        self._seq += 1
        record = {
            "seq": self._seq,
            "timestamp": time.time(),
            "epsilon": epsilon,
            "delta": delta,
            "epsilon_spent_total": self.epsilon_spent,
            "epsilon_remaining": self.epsilon_remaining,
            "note": note,
        }
        self._append(record)
        return record

    def _append(self, record: dict) -> None:
        if self._spill is not None:
            self._spill.write(json.dumps(record, ensure_ascii=False) + "\n")
        else:
            self._records.append(record)

    @property
    def record_count(self) -> int:
        return self._seq

    def record_free(self, note: str = "") -> dict:
        """记录一次零消耗事件（如重复切片命中缓存）。"""
        self._seq += 1
        record = {
            "seq": self._seq,
            "timestamp": time.time(),
            "epsilon": 0.0,
            "delta": 0.0,
            "epsilon_spent_total": self.epsilon_spent,
            "epsilon_remaining": self.epsilon_remaining,
            "note": note,
        }
        self._append(record)
        return record

    def records(self) -> list:
        return list(self._records)

    def export_json(self, path: str) -> None:
        """导出消耗记录（每轮一行 JSON，含表头行）。"""
        header = {
            "type": "header",
            "epsilon_total": self.epsilon_total,
            "delta_total": self.delta_total,
        }
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(header, ensure_ascii=False) + "\n")
            for rec in self._records:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def close(self) -> None:
        if self._spill is not None:
            self._spill.close()
            self._spill = None

    def summary(self) -> str:
        return (f"epsilon: {self.epsilon_spent:.6f}/{self.epsilon_total:.6f} "
                f"已消耗（剩余 {self.epsilon_remaining:.6f}），"
                f"delta: {self.delta_spent:.6g}/{self.delta_total:.6g}，"
                f"共 {self._seq} 条记录")
