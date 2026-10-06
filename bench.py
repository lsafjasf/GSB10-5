"""基准：10 万处调用、1000 个依赖的可达性分析耗时与内存峰值。

用法: python3 bench.py [--deps N] [--app-modules M] [--out PATH]

合成负载：
* N 个依赖包，每包 1 个模块、10 个函数，每函数调用同模块其余 9 个函数
  -> 依赖内调用点 90 * N 处；
* M 个应用模块，每模块 25 个函数链式调用
  -> 应用内调用点 24 * M 处；
* 默认 N=1000, M=500 -> 约 102,000 处调用点；
* 漏洞清单 1000 条：dep_0000.f0 可达（经 app_000.main 间接到达），
  其余 999 条落在从未被引用的依赖上。
"""

import argparse
import json
import resource
import time
import tracemalloc

from reachability import Analyzer, Vulnerability, build_graph

FUNCS_PER_DEP = 10
FUNCS_PER_APP_MODULE = 25


def gen_dep_source(index: int) -> str:
    lines = []
    for f in range(FUNCS_PER_DEP):
        lines.append(f"def f{f}(x):")
        for g in range(FUNCS_PER_DEP):
            if g != f:
                lines.append(f"    x = f{g}(x)")
        lines.append("    return x")
        lines.append("")
    return "\n".join(lines)


def gen_app_module(index: int, reach_dep0: bool) -> str:
    lines = []
    if reach_dep0:
        lines.append("from dep_0000 import f9")
        lines.append("")
    for f in range(FUNCS_PER_APP_MODULE):
        lines.append(f"def a{f}(x):")
        if f + 1 < FUNCS_PER_APP_MODULE:
            lines.append(f"    return a{f + 1}(x)")
        elif reach_dep0:
            lines.append("    return f9(x)")
        else:
            lines.append("    return x")
        lines.append("")
    lines.append("def main():")
    lines.append("    return a0(1)")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deps", type=int, default=1000)
    parser.add_argument("--app-modules", type=int, default=500)
    parser.add_argument("--out", default="out/benchmark.json")
    args = parser.parse_args()

    timings = {}

    t0 = time.perf_counter()
    files = []
    for i in range(args.deps):
        name = f"dep_{i:04d}"
        files.append((name, f"deps/{name}.py", gen_dep_source(i)))
    for i in range(args.app_modules):
        name = f"app_{i:03d}"
        files.append((name, f"app/{name}.py",
                      gen_app_module(i, reach_dep0=(i == 0))))
    vulns = [Vulnerability(f"BENCH-{i:04d}", f"dep_{i:04d}",
                           (f"dep_{i:04d}.f0",))
             for i in range(args.deps)]
    installed = [f"dep_{i:04d}" for i in range(args.deps)]
    timings["generate_seconds"] = round(time.perf_counter() - t0, 4)

    tracemalloc.start()
    t0 = time.perf_counter()
    graph = build_graph(files)
    timings["build_graph_seconds"] = round(time.perf_counter() - t0, 4)

    t0 = time.perf_counter()
    analyzer = Analyzer(graph, vulns, installed_deps=installed,
                        entries=["app_000.main"])
    result = analyzer.analyze()
    timings["analyze_seconds"] = round(time.perf_counter() - t0, 4)

    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    max_rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    status_counts = {}
    for verdict in result.verdicts:
        status_counts[verdict.status] = status_counts.get(verdict.status, 0) + 1

    report = {
        "workload": {
            "dependencies": args.deps,
            "app_modules": args.app_modules,
            "functions": len(graph.functions),
            "call_sites": graph.call_site_count,
            "vulnerabilities": len(vulns),
        },
        "timings": timings,
        "memory": {
            "tracemalloc_peak_mb": round(peak / 1024 / 1024, 1),
            "process_max_rss_mb": round(max_rss_kb / 1024, 1),
        },
        "verdicts": status_counts,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        print(f"已写入 {args.out}")


if __name__ == "__main__":
    main()
