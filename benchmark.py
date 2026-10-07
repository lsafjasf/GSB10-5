"""规模基准与一致率校验 (仅标准库)。

规模: 10 万条配置, 约 100 万条引用, 构成为:
  - 835,000 条 服务->配置 静态引用
  -  40,000 条 模块->配置 静态引用
  -  40,000 条 服务->模块 依赖边 (与上者组成 服务->模块->配置 间接链)
  -  50,000 条 配置->配置 模板间接引用
  -  30,000 条 动态拼接引用边 (15,000 个模式, 变量域 env∈{dev,prod}, 展开×2)
  -   5,000 条 变量域未知的动态拼接引用 (不建边, 进待人工确认库存)
合计 1,000,000。

一致率: 抽样变更项, 以独立的暴力 BFS(直接遍历原始引用记录)为基准答案,
与本库图分析结果逐项比对, 给出精确率/召回率/完全一致样本占比;
待人工确认清单同样独立比对。

运行: python3 benchmark.py [--samples 200]
"""

import argparse
import random
import resource
import time
import tracemalloc

from cfgimpact import ImpactGraph, ImpactAnalyzer
from cfgimpact.graph import pattern_struct_match

N_REGULAR = 80_000          # 普通配置
N_DYN_PER_ENV = 10_000      # 每个 env 的动态配置, envs = dev/prod
N_CONFIGS = N_REGULAR + 2 * N_DYN_PER_ENV  # 100,000
N_STATIC = 835_000
N_MOD_STATIC = 40_000
N_DEP = 40_000
N_TEMPLATE = 50_000
N_DYN_RES = 15_000
N_DYN_UNRES = 5_000
N_SERVICES = 20_000
N_MODULES = 5_000
N_SCOPES = 500
TOTAL_REFS = N_STATIC + N_MOD_STATIC + N_DEP + N_TEMPLATE + 2 * N_DYN_RES + N_DYN_UNRES


def generate(seed=42):
    rng = random.Random(seed)
    regular = [f"svc{i % N_SCOPES}.key{i}" for i in range(N_REGULAR)]
    dyn_configs = [f"dyn.{env}.key{i}"
                   for env in ("dev", "prod") for i in range(N_DYN_PER_ENV)]
    configs = regular + dyn_configs
    services = [f"service{j}" for j in range(N_SERVICES)]
    modules = [f"module{k}" for k in range(N_MODULES)]

    static_edges = []
    dep_edges = []
    template_pairs = []
    dyn_resolved = []
    dyn_unresolved = []

    for _ in range(N_STATIC):
        static_edges.append((rng.choice(services), rng.choice(configs)))
    for _ in range(N_MOD_STATIC):
        static_edges.append((rng.choice(modules), rng.choice(configs)))
    for _ in range(N_DEP):
        dep_edges.append((rng.choice(services), rng.choice(modules)))
    for _ in range(N_TEMPLATE):
        template_pairs.append(tuple(rng.sample(configs, 2)))
    for _ in range(N_DYN_RES):
        i = rng.randrange(N_DYN_PER_ENV)
        dyn_resolved.append((rng.choice(modules), i))
    for _ in range(N_DYN_UNRES):
        dyn_unresolved.append(
            (rng.choice(services), f"feat.{{flag}}.key{rng.randrange(100_000)}")
        )

    return {
        "configs": configs, "services": services, "modules": modules,
        "static_edges": static_edges, "dep_edges": dep_edges,
        "template_pairs": template_pairs,
        "dyn_resolved": dyn_resolved, "dyn_unresolved": dyn_unresolved,
    }


def build_graph(data):
    g = ImpactGraph()
    for c in data["configs"]:
        g.add_config(c)
    for src, dst in data["static_edges"]:
        g.add_static(src, dst)
    for src, dst in data["dep_edges"]:
        g.add_dependency(src, dst)
    for a, b in data["template_pairs"]:
        g.add_config(a, f"prefix-${{{b}}}-suffix")
    for src, i in data["dyn_resolved"]:
        g.add_dynamic(src, f"dyn.{{env}}.key{i}", domain={"env": ["dev", "prod"]})
    for src, pattern in data["dyn_unresolved"]:
        g.add_dynamic(src, pattern)
    return g.finalize()


def resolved_edges(data):
    """暴力 BFS 用的全部已解析边。"""
    edges = list(data["static_edges"])
    edges.extend(data["dep_edges"])
    edges.extend(data["template_pairs"])
    for src, i in data["dyn_resolved"]:
        edges.append((src, f"dyn.dev.key{i}"))
        edges.append((src, f"dyn.prod.key{i}"))
    return edges


def build_adjacency(edges):
    adj = {}
    for src, dst in edges:
        adj.setdefault(dst, []).append(src)
    return adj


def brute_force(adj, dyn_unresolved, changed):
    """基准答案: 独立实现, 直接对原始引用记录做可达性 BFS。"""
    seen = set(changed)
    stack = list(changed)
    while stack:
        u = stack.pop()
        for v in adj.get(u, ()):
            if v not in seen:
                seen.add(v)
                stack.append(v)
    affected = seen - set(changed)
    manual = {(s, p) for s, p in dyn_unresolved
              if any(pattern_struct_match(p, c) for c in changed)}
    return affected, manual


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=200)
    args = ap.parse_args()

    print("生成数据: 10 万配置 / 100 万引用 ...")
    data = generate()
    print(f"原始引用合计: {TOTAL_REFS} (含待人工确认 {N_DYN_UNRES} 条)")

    tracemalloc.start()
    t0 = time.perf_counter()
    g = build_graph(data)
    t_build = time.perf_counter() - t0
    analyzer = ImpactAnalyzer(g)

    edges = resolved_edges(data)
    adj = build_adjacency(edges)
    samples = random.Random(7).sample(data["configs"], args.samples)
    timings = []
    tp = fp = fn = 0
    manual_tp = manual_fp = manual_fn = 0
    exact = 0
    sizes = []

    for changed in samples:
        t1 = time.perf_counter()
        rep = analyzer.analyze([changed])
        timings.append(time.perf_counter() - t1)

        gt_affected, gt_manual = brute_force(
            adj, data["dyn_unresolved"], [changed]
        )
        got = rep.affected
        sizes.append(len(got))
        tp += len(got & gt_affected)
        fp += len(got - gt_affected)
        fn += len(gt_affected - got)

        got_manual = {(m.source, m.pattern) for m in rep.manual_confirm}
        manual_tp += len(got_manual & gt_manual)
        manual_fp += len(got_manual - gt_manual)
        manual_fn += len(gt_manual - got_manual)
        if got == gt_affected and got_manual == gt_manual:
            exact += 1

    _, peak_mem = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    m_prec = manual_tp / (manual_tp + manual_fp) if manual_tp + manual_fp else 1.0
    m_rec = manual_tp / (manual_tp + manual_fn) if manual_tp + manual_fn else 1.0
    timings.sort()
    p95 = timings[int(args.samples * 0.95) - 1]

    print()
    print("===== 规模 =====")
    print(f"配置项: {len(data['configs'])}  服务: {len(data['services'])}  "
          f"模块: {len(data['modules'])}")
    print(f"图节点: {len(g.names)}  图边: {g.edge_count}  "
          f"未解析动态引用: {len(g.unresolved)}")
    print()
    print("===== 耗时 =====")
    print(f"建图(含动态展开/求交): {t_build:.3f} s")
    print(f"分析 {args.samples} 个变更样本: 合计 {sum(timings):.3f} s, "
          f"平均 {sum(timings)/args.samples*1000:.3f} ms/次, "
          f"P95 {p95*1000:.3f} ms/次")
    print()
    print("===== 内存峰值 =====")
    print(f"tracemalloc Python 对象峰值: {peak_mem/1024/1024:.1f} MB")
    print(f"进程 RSS 峰值(ru_maxrss): {rss_kb/1024:.1f} MB")
    print()
    print("===== 一致率(对独立暴力 BFS 基准答案) =====")
    print(f"样本数: {args.samples}, 结果完全一致样本: {exact} "
          f"({exact/args.samples*100:.1f}%)")
    print(f"影响集合   精确率: {precision*100:.2f}%  召回率: {recall*100:.2f}%")
    print(f"待人工确认 精确率: {m_prec*100:.2f}%  召回率: {m_rec*100:.2f}%")
    print(f"平均受影响节点数: {sum(sizes)/len(sizes):.1f}, 最大: {max(sizes)}")


if __name__ == "__main__":
    main()
