# -*- coding: utf-8 -*-
"""基准测试：10 万条配置、100 万条引用。

数据集形态贴近真实配置中心：
  - 配置名带层级（grp{g}.key{i}），动态拼接模式为 grp{g}.*
  - 派生边构成有界深度（<=3 层）的链/星形，而非随机大图：
    真实配置派生绝大多数是“基础配置 -> 少量派生配置”

度量（两遍运行，避免 tracemalloc 污染计时）：
  第 1 遍：建图耗时、单次/平均分析耗时、进程 RSS 峰值
  第 2 遍：tracemalloc 统计 Python 堆内存峰值

运行: python3 -m config_impact.benchmark
"""

from __future__ import annotations

import os
import random
import resource
import sys
import time
import tracemalloc

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config_impact.impact_analyzer import ImpactAnalyzer, ConfigKey

N_CONFIGS = 100_000
N_REFS = 1_000_000
PCT_STATIC, PCT_DERIVE = 0.70, 0.25     # 其余 5% 为动态拼接
N_NAMESPACES = 1_000
N_GROUPS = 500
N_SERVICES = 5_000


def gen_dataset(seed=42):
    """生成 (configs, refs)。派生边: 基础配置(20%) -> 派生配置，深度<=3。"""
    rng = random.Random(seed)
    configs = [(f"ns{i % N_NAMESPACES}", f"grp{i % N_GROUPS}.key{i}")
               for i in range(N_CONFIGS)]
    n_static = int(N_REFS * PCT_STATIC)
    n_derive = int(N_REFS * PCT_DERIVE)
    n_dynamic = N_REFS - n_static - n_derive

    refs = []
    for _ in range(n_static):
        ns, name = configs[rng.randrange(N_CONFIGS)]
        refs.append(("static", f"svc{rng.randrange(N_SERVICES)}", ns, name))

    # 严格 4 层 DAG: 基础 -> 派生1 -> 派生2 -> 叶子，叶子无出边，
    # 不存在回边，派生链深度 <=3，分量大小有界（贴近真实配置派生）
    pools = [[c for c in configs if int(c[1].split("key")[1]) % 5 == k]
             for k in (0, 1, 2)]
    leaves = [c for c in configs if int(c[1].split("key")[1]) % 5 in (3, 4)]
    targets = (pools[1], pools[2], leaves)
    for _ in range(n_derive):
        layer = rng.randrange(3)
        src = pools[layer][rng.randrange(len(pools[layer]))]
        tgt_pool = targets[layer]
        dst = tgt_pool[rng.randrange(len(tgt_pool))]
        refs.append(("derive", src, dst))

    for _ in range(n_dynamic):
        ns = f"ns{rng.randrange(N_NAMESPACES)}"
        refs.append(("dynamic", f"svc{rng.randrange(N_SERVICES)}",
                     ns, f"grp{rng.randrange(N_GROUPS)}.*",
                     f'"grp{rng.randrange(N_GROUPS)}." + env + ""'))
    return configs, refs


def build(configs, refs):
    a = ImpactAnalyzer()
    for ns, name in configs:
        a.add_config(ns, name)
    for ref in refs:
        kind = ref[0]
        if kind == "static":
            a.add_static_ref(ref[1], ref[2], ref[3])
        elif kind == "derive":
            (s_ns, s_name), (d_ns, d_name) = ref[1], ref[2]
            a.add_derive_ref(s_ns, s_name, d_ns, d_name)
        else:
            a.add_dynamic_ref(ref[1], ref[2], ref[3], ref[4])
    return a


def main():
    configs, refs = gen_dataset()
    n_static = int(N_REFS * PCT_STATIC)
    n_derive = int(N_REFS * PCT_DERIVE)
    print(f"数据集: {N_CONFIGS:,} 配置, {N_REFS:,}引用 "
          f"(静态 {n_static:,} / 派生 {n_derive:,} / 动态 {N_REFS-n_static-n_derive:,})",
          flush=True)

    # ---- 第 1 遍：计时 + RSS ----
    t0 = time.perf_counter()
    a = build(configs, refs)
    t_build = time.perf_counter() - t0

    rng = random.Random(7)
    probes = [configs[rng.randrange(N_CONFIGS)] for _ in range(200)]
    t0 = time.perf_counter()
    worst = 0.0
    sample = None
    for ns, name in probes:
        t1 = time.perf_counter()
        r = a.analyze(ConfigKey(ns, name))
        dt = time.perf_counter() - t1
        if dt > worst:
            worst, sample = dt, r
    t_avg = (time.perf_counter() - t0) / len(probes)
    # 针对性验证动态模式在大数据量下可命中：
    # 在同命名空间内枚举动态模式与配置名，找到一对匹配项
    from fnmatch import fnmatchcase as _fm
    d_ns = d_pat = hit_name = None
    by_ns = {}
    for ns_c, name_c in configs:
        by_ns.setdefault(ns_c, []).append(name_c)
    for ns_d, entries in a._dynamic.items():
        if ns_d == "*":
            continue
        names = by_ns.get(ns_d, ())
        found = False
        for _, pat, _, _ in entries:
            for name_c in names:
                if _fm(name_c, pat):
                    d_ns, d_pat, hit_name = ns_d, pat, name_c
                    found = True
                    break
            if found:
                break
        if found:
            break
    dyn_report = a.analyze(ConfigKey(d_ns, hit_name))
    rss_mib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

    stats = a.stats()
    print(f"\n[第 1 遍] 计时（未开 tracemalloc）")
    print(f"  图规模:          {stats['configs']:,} 配置节点, "
          f"{stats['total_refs']:,} 条引用边")
    print(f"  建图耗时:        {t_build:.3f} s")
    print(f"  分析耗时(均值):  {t_avg*1000:.3f} ms   (200 次随机变更)")
    print(f"  分析耗时(最差):  {worst*1000:.3f} ms   "
          f"(直接 {len(sample.direct)} / 间接 {len(sample.indirect)} "
          f"/ 待确认 {len(sample.pending)})")
    print(f"  动态命中验证:    变更 {d_ns}:{hit_name} -> "
          f"待确认 {len(dyn_report.pending)} 条动态引用")
    print(f"  内存峰值(RSS):   {rss_mib:.1f} MiB (ru_maxrss, 含解释器与数据集)")

    del a
    # ---- 第 2 遍：tracemalloc 堆峰值（仅建图，不计时）----
    tracemalloc.start()
    a2 = build(configs, refs)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"\n[第 2 遍] 内存（tracemalloc，仅度量不计时）")
    print(f"  内存峰值(堆):    {peak/1024/1024:.1f} MiB "
          f"(分析器数据结构, 不含数据集生成开销)")


if __name__ == "__main__":
    main()
