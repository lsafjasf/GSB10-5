"""评估结果 vs 模拟演练结果的偏差计算。"""

from __future__ import annotations

import math

from .assessment import assess
from .simulate import simulate_drill
from .topology import Topology


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def compare_once(topo: Topology, injected: set[str], n_requests: int = 100_000,
                 seed: int = 7) -> dict:
    est = assess(topo, injected)
    obs = simulate_drill(topo, injected, n_requests=n_requests, seed=seed)

    common = set(est.service_loss) & set(obs.observed_loss)
    diffs = [est.service_loss[s] - obs.observed_loss[s] for s in common]
    rmse = math.sqrt(sum(d * d for d in diffs) / len(diffs)) if diffs else 0.0
    mae = sum(abs(d) for d in diffs) / len(diffs) if diffs else 0.0

    return {
        "estimate": est.summary(),
        "observed_drill": obs.summary(),
        "deviation": {
            "affected_user_fraction_estimate": round(
                est.affected_user_fraction, 6),
            "affected_user_fraction_observed": round(
                obs.affected_user_fraction, 6),
            "absolute_error": round(
                abs(est.affected_user_fraction - obs.affected_user_fraction),
                6),
            "relative_error_pct": (
                round(100.0 * abs(est.affected_user_fraction
                                  - obs.affected_user_fraction)
                      / obs.affected_user_fraction, 2)
                if obs.affected_user_fraction > 1e-9 else 0.0),
            "down_set_jaccard": round(_jaccard(est.down, obs.observed_down), 4),
            "impacted_set_jaccard": round(
                _jaccard(est.impacted, obs.observed_impacted), 4),
            "down_estimate_only": sorted(est.down - obs.observed_down),
            "down_observed_only": sorted(obs.observed_down - est.down),
            "service_loss_rmse": round(rmse, 6),
            "service_loss_mae": round(mae, 6),
        },
    }


DEVIATION_SOURCES = [
    "故障切换失败：评估假设流量可完全切到健康副本，实际受连接池/配置/会话"
    "粘连等影响，按 DependencyGroup.fail_over_fail 产生额外损失",
    "采样噪声：模拟按有限次请求抽样，错误率是估计值（标准误差约 "
    "sqrt(p(1-p)/N)，N=10万时约 0.16% 以内）",
    "观测阈值：演练只能从监控错误率判定异常，阈值 >=99% 才记为 down，"
    "轻微受损服务可能被漏报，拓扑推导则无此损失",
    "重试/熔断/超时未建模：评估不包含重试放大、雪崩与自动熔断行为",
    "流量比例为静态配置：实际入口权重与依赖流量占比随时间波动",
]
