"""实际故障演练模拟器：逐请求蒙特卡洛，作为“真实演练结果”的对照基线。

与确定性评估（assessment.py）刻意保持以下差异，用于量化偏差：

1. 评估假设冗余组故障切换完美；模拟中每个失效副本触发一次切换，
   有 fail_over_fail 的概率切换失败（连接池/配置/会话粘连等现实因素）。
2. 评估按 traffic_share 求期望损失；模拟按有限次请求采样，存在抽样波动。
3. 评估按拓扑推导“必然 down”；模拟只能从监控观测（错误率阈值）判定
   服务异常，轻微受损可能漏报。
4. 模拟按随机顺序逐个尝试副本（等价于负载均衡 + 同请求内重试）。
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .topology import Topology

MAX_DEPTH = 64
DOWN_THRESHOLD = 0.99
IMPACT_THRESHOLD = 1e-4


@dataclass
class DrillResult:
    n_requests: int
    failed_requests: int
    observed_loss: dict[str, float]
    observed_down: set[str]
    observed_impacted: set[str]
    entry_sampled: dict[str, int]

    @property
    def affected_user_fraction(self) -> float:
        return self.failed_requests / self.n_requests

    def summary(self) -> dict:
        return {
            "n_requests": self.n_requests,
            "failed_requests": self.failed_requests,
            "affected_user_fraction": round(self.affected_user_fraction, 6),
            "services_observed_down": len(self.observed_down),
            "services_observed_impacted": len(self.observed_impacted),
        }


def simulate_drill(
    topo: Topology,
    injected_domains: set[str],
    n_requests: int = 100_000,
    seed: int = 7,
) -> DrillResult:
    rng = random.Random(seed)
    faulted = {sid for sid, svc in topo.services.items()
               if svc.fail_domain in injected_domains}

    entries = list(topo.entry_services)
    weights = [topo.services[e].entry_weight for e in entries]

    req_count: dict[str, int] = {}
    fail_count: dict[str, int] = {}
    entry_sampled: dict[str, int] = {}
    failed_requests = 0

    def serve(sid: str, stack: set[str], depth: int) -> bool:
        """处理一个到达 sid 的请求；False 表示该请求失败。"""
        if depth > MAX_DEPTH:
            return False
        if sid in faulted:
            return False
        if sid in stack:
            return True
        svc = topo.services[sid]
        stack.add(sid)
        req_count[sid] = req_count.get(sid, 0) + 1
        try:
            for grp in svc.deps:
                if rng.random() >= grp.traffic_share:
                    continue
                order = list(grp.providers)
                rng.shuffle(order)
                down_seen = any(pid in faulted for pid in grp.providers)
                if down_seen and grp.fail_over_fail > 0.0 and \
                        rng.random() < grp.fail_over_fail:
                    fail_count[sid] = fail_count.get(sid, 0) + 1
                    return False
                served = False
                for pid in order:
                    if pid in faulted:
                        continue
                    if serve(pid, stack, depth + 1):
                        served = True
                        break
                if not served:
                    fail_count[sid] = fail_count.get(sid, 0) + 1
                    return False
            return True
        finally:
            stack.discard(sid)

    entries_acc: list[tuple[str, float]] = []
    acc = 0.0
    for sid, w in zip(entries, weights):
        acc += w
        entries_acc.append((sid, acc))

    def pick_entry() -> str:
        u = rng.random()
        for sid, cutoff in entries_acc:
            if u <= cutoff:
                return sid
        return entries_acc[-1][0]

    for _ in range(n_requests):
        entry = pick_entry()
        entry_sampled[entry] = entry_sampled.get(entry, 0) + 1
        ok = serve(entry, set(), 1)
        if not ok:
            failed_requests += 1

    observed_loss = {}
    for sid, n in req_count.items():
        observed_loss[sid] = fail_count.get(sid, 0) / n
    observed_down = {sid for sid, v in observed_loss.items()
                     if v >= DOWN_THRESHOLD}
    observed_impacted = {sid for sid, v in observed_loss.items()
                         if v >= IMPACT_THRESHOLD}
    observed_down |= faulted
    observed_impacted |= faulted

    return DrillResult(
        n_requests=n_requests, failed_requests=failed_requests,
        observed_loss=observed_loss, observed_down=observed_down,
        observed_impacted=observed_impacted, entry_sampled=entry_sampled,
    )
