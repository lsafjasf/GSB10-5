"""
演示：四类典型故障场景的影响评估 + 与"实际演练结果"的偏差对比。

拓扑（迷你电商，入口权重 web=0.5 / app=0.5）：

  web ──hard0.5──▶ order ──hard0.5──▶ pay ──组1.0──▶ [paydb-1, paydb-2]  ← 同 az-a（隐性耦合）
  web ──hard0.3──▶ search ──hard1.0──▶ searchdb
  web ──hard0.2──▶ session (az-a)
  app ──hard0.7──▶ order ──组0.4──▶ [stock-a(az-a), stock-b(az-b)]       ← 跨 AZ 多活
  app ──soft0.3──▶ feed ──hard1.0──▶ feeddb
  order ─soft0.1─▶ reco

运行：python3 demo.py
输出：控制台报告 + data/impact_data.json + data/deviation_data.json
"""

from __future__ import annotations

import json
import os

from blast_radius import DrillResult, Topology, compare, estimate

TOPO = {
    "services": {
        "web":      {"entry_weight": 0.5, "zone": "az-a", "deps": [
            {"target": "order", "ratio": 0.5},
            {"target": "search", "ratio": 0.3},
            {"target": "session", "ratio": 0.2},
        ]},
        "app":      {"entry_weight": 0.5, "zone": "az-b", "deps": [
            {"target": "order", "ratio": 0.7},
            {"target": "feed", "ratio": 0.3, "kind": "soft"},
        ]},
        "order":    {"zone": "az-a", "deps": [
            {"target": "pay", "ratio": 0.5},
            {"target": "reco", "ratio": 0.1, "kind": "soft"},
        ], "groups": [
            {"id": "stock-grp", "ratio": 0.4, "members": [
                {"service": "stock-a"}, {"service": "stock-b"}]},
        ]},
        "pay":      {"zone": "az-b", "groups": [
            {"id": "paydb-grp", "ratio": 1.0, "members": [
                {"service": "paydb-1"}, {"service": "paydb-2"}]},
        ]},
        "search":   {"zone": "az-b", "deps": [{"target": "searchdb", "ratio": 1.0}]},
        "session":  {"zone": "az-a"},
        "feed":     {"zone": "az-c", "deps": [{"target": "feeddb", "ratio": 1.0}]},
        "reco":     {"zone": "az-c"},
        "stock-a":  {"zone": "az-a"},
        "stock-b":  {"zone": "az-b"},
        "paydb-1":  {"zone": "az-a"},
        "paydb-2":  {"zone": "az-a"},
        "searchdb": {"zone": "az-c"},
        "feeddb":   {"zone": "az-c"},
    }
}


def show(name: str, faults: list[str], report) -> dict:
    d = report.to_dict()
    print(f"\n=== {name} ===")
    print(f"故障注入: {faults}")
    print(f"受影响用户比例: {d['affected_user_ratio']:.2%}")
    print(f"受影响服务数: {d['services_affected']} (不可用 {d['services_down']})")
    print(f"受影响入口: {d['affected_entries'] or '无'}")
    print(f"关键路径数: {d['critical_path_count']}"
          f"{'' if d['critical_path_exact'] else '（有环，宏路径计数）'}")
    if d["broken_groups"]:
        for g in d["broken_groups"]:
            tag = "【冗余同时失效】" if g["correlated"] else "【冗余组失效】"
            print(f"{tag} 组 {g['group_id']} 成员 {g['members_down']}"
                  f" 同故障域={g['shared_zone']}")
    return d


def main() -> None:
    topo = Topology.from_dict(TOPO)
    impact: dict[str, dict] = {}
    deviation: dict[str, dict] = {}

    # 场景 1：无影响 —— 冗余组单成员挂，多活兜底，用户无感
    r1 = estimate(topo, ["stock-a"])
    impact["1_no_impact"] = show("场景1 无影响（冗余单副本故障）", ["stock-a"], r1)

    # 场景 2：单点影响 —— searchdb 单点，只烧到 web 的搜索流量
    r2 = estimate(topo, ["searchdb"])
    impact["2_single_point"] = show("场景2 单点影响（searchdb 故障）", ["searchdb"], r2)

    # 场景 3：级联影响 —— pay 单点，沿 order 级联到两个入口
    r3 = estimate(topo, ["pay"])
    impact["3_cascade"] = show("场景3 级联影响（pay 故障）", ["pay"], r3)

    # 场景 4：冗余同时失效 —— paydb 双副本同 AZ，一起挂
    r4 = estimate(topo, ["paydb-1", "paydb-2"])
    impact["4_correlated_redundancy"] = show(
        "场景4 冗余同时失效（paydb 双副本同 az-a）", ["paydb-1", "paydb-2"], r4)

    # ---- 偏差对比 4a：实际演练多炸了一个服务（故障域成员清单不全）----
    actual4 = estimate(topo, ["paydb-1", "paydb-2", "session"])  # session 同 AZ 被连带
    drill4 = DrillResult(
        affected_services=set(actual4.degraded),
        user_ratio=actual4.affected_user_ratio,
        entry_health={s: actual4.health[s] for s in ("web", "app")},
        critical_path_count=actual4.critical_path_count,
        fault_domain="az-a",
    )
    c4 = compare(topo, r4, drill4)
    deviation["4a_domain_collateral"] = c4.to_dict()
    print("\n--- 偏差对比 4a：估算(只注入paydb) vs 实测(session被同AZ连带) ---")
    print(f"用户比例 估算 {c4.predicted_user_ratio:.2%} / 实测 {c4.observed_user_ratio:.2%}"
          f" / 偏差 {c4.gap_pp:+.2f}pp")
    print(f"服务集合 precision={c4.precision:.2f} recall={c4.recall:.2f} F1={c4.f1:.2f}")
    print(f"关键路径 估算 {c4.predicted_paths} / 实测 {c4.observed_paths}"
          f" / 偏差 {c4.path_gap_pct:+.1f}%")
    for a in c4.attribution:
        print(f"  偏差来源: {a.source} 贡献 {a.share_pp:+.2f}pp 入口 {a.entries}")

    # ---- 偏差对比 5：模型高估 —— 实际有降级预案（强依赖实为弱依赖）----
    r5 = estimate(topo, ["stock-a", "stock-b"])  # 模型：stock 组全灭 -> 强依赖受损
    impact["5_group_wipeout"] = show(
        "场景5 冗余组全灭（stock 双副本）", ["stock-a", "stock-b"], r5)
    actual_world = Topology.from_dict(TOPO)
    for g in actual_world.services["order"].groups:
        if g.id == "stock-grp":
            g.kind, g.degraded_penalty = "soft", 0.1  # 实际有缓存兜底/降级预案
    actual5 = estimate(actual_world, ["stock-a", "stock-b"])
    drill5 = DrillResult(
        affected_services=set(actual5.degraded),
        user_ratio=actual5.affected_user_ratio,
        entry_health={s: actual5.health[s] for s in ("web", "app")},
        critical_path_count=actual5.critical_path_count,
    )
    c5 = compare(topo, r5, drill5)
    deviation["5_overestimate_fallback"] = c5.to_dict()
    print("\n--- 偏差对比 5：估算(强依赖) vs 实测(降级预案生效) ---")
    print(f"用户比例 估算 {c5.predicted_user_ratio:.2%} / 实测 {c5.observed_user_ratio:.2%}"
          f" / 偏差 {c5.gap_pp:+.2f}pp")
    print(f"服务集合 precision={c5.precision:.2f} recall={c5.recall:.2f} F1={c5.f1:.2f}")
    for a in c5.attribution:
        print(f"  偏差来源: {a.source} 贡献 {a.share_pp:+.2f}pp 入口 {a.entries}")

    os.makedirs("data", exist_ok=True)
    with open("data/impact_data.json", "w", encoding="utf-8") as f:
        json.dump(impact, f, ensure_ascii=False, indent=2)
    with open("data/deviation_data.json", "w", encoding="utf-8") as f:
        json.dump(deviation, f, ensure_ascii=False, indent=2)
    print("\n已写出 data/impact_data.json 与 data/deviation_data.json")


if __name__ == "__main__":
    main()
