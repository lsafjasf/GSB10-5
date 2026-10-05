"""
性能基准：1 万服务 / 10 万依赖的评估耗时与内存峰值。

运行：python3 bench.py
输出：控制台表格 + data/benchmark.json
"""

from __future__ import annotations

import json
import os
import time
import tracemalloc

from blast_radius import estimate, generate_scale_topology


def timed(fn):
    tracemalloc.start()
    t0 = time.perf_counter()
    result = fn()
    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return result, elapsed, peak / 1024 / 1024


def main() -> None:
    print("生成拓扑：10,000 服务 / 100,000 依赖 ...")
    topo, t_build, m_build = timed(generate_scale_topology)
    n_edges = sum(len(sv.deps) for sv in topo.services.values())
    n_groups = sum(len(sv.groups) for sv in topo.services.values())
    print(f"实际边数 {n_edges}（含冗余组 {n_groups} 组 × 3 成员）"
          f"  构建 {t_build:.2f}s / 峰值 {m_build:.1f}MB")

    # 单服务故障（一个入口服务 + 一个深层服务各测一次，取入口为例）
    (r1, t1, m1) = timed(lambda: estimate(topo, ["svc-00010"]))
    # 整 AZ 故障（约 1/3 服务 + 隐性耦合冗余组被同时命中）
    az_a = [sid for sid, sv in topo.services.items() if sv.zone == "az-a"]
    (r2, t2, m2) = timed(lambda: estimate(topo, az_a))

    rows = [
        ("拓扑构建", t_build, m_build, "-"),
        ("单服务故障评估", t1, m1,
         f"受影响用户 {r1.affected_user_ratio:.2%} / 服务 {len(r1.degraded)}"
         f" / 关键路径 {r1.critical_path_count}"),
        (f"整AZ故障评估({len(az_a)}个服务)", t2, m2,
         f"受影响用户 {r2.affected_user_ratio:.2%} / 服务 {len(r2.degraded)}"
         f" / 关键路径 {r2.critical_path_count}"
         f" / 冗余组失效 {len(r2.broken_groups)}"),
    ]
    print(f"\n{'项目':<24}{'耗时(s)':>10}{'内存峰值(MB)':>14}  结果摘要")
    for name, t, m, note in rows:
        print(f"{name:<24}{t:>10.3f}{m:>14.1f}  {note}")

    correlated = sum(1 for g in r2.broken_groups if g.correlated)
    print(f"\n整AZ故障中识别到冗余同时失效的组: {correlated} / {len(r2.broken_groups)}")

    os.makedirs("data", exist_ok=True)
    az = r2.to_dict()
    az_summary = {k: v for k, v in az.items()
                  if k not in ("down", "degraded", "affected_entries", "faults")}
    az_summary["broken_groups"] = az["broken_groups"][:5]
    with open("data/benchmark.json", "w", encoding="utf-8") as f:
        json.dump({
            "scale": {"services": len(topo.services), "edges": n_edges,
                      "redundancy_groups": n_groups},
            "build": {"seconds": round(t_build, 3), "peak_mb": round(m_build, 1)},
            "single_fault": {"seconds": round(t1, 3), "peak_mb": round(m1, 1),
                             "report": r1.to_dict()},
            "az_fault": {"faults": len(az_a), "seconds": round(t2, 3),
                         "peak_mb": round(m2, 1),
                         "report": az_summary,
                         "correlated_broken_groups": correlated},
        }, f, ensure_ascii=False, indent=2)
    print("已写出 data/benchmark.json")


if __name__ == "__main__":
    main()
