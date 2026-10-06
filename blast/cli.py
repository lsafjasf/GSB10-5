from __future__ import annotations

import argparse
import json
import os

from .assessment import assess
from .benchmark import run_benchmark
from .compare import DEVIATION_SOURCES, compare_once
from .topology import SCENARIOS


def _scenario(name: str):
    if name not in SCENARIOS:
        raise SystemExit(f"未知场景 {name}，可选: {', '.join(SCENARIOS)}")
    return SCENARIOS[name]()


def cmd_scenarios(_args) -> None:
    print(json.dumps(sorted(SCENARIOS), ensure_ascii=False, indent=2))


def cmd_assess(args) -> None:
    topo, injected = _scenario(args.name)
    result = assess(topo, injected)
    payload = {
        "scenario": args.name,
        "injected_domains": sorted(injected),
        "summary": result.summary(),
        "detail": result.detail(),
        "critical_path_count": result.critical_path_count,
        "critical_paths_truncated": result.critical_paths_truncated,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_compare(args) -> None:
    topo, injected = _scenario(args.name)
    payload = {
        "scenario": args.name,
        "injected_domains": sorted(injected),
        "n_requests": args.requests,
        **compare_once(topo, injected, n_requests=args.requests),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_compare_all(args) -> None:
    payload = {}
    for name in SCENARIOS:
        topo, injected = SCENARIOS[name]()
        payload[name] = compare_once(topo, injected,
                                     n_requests=args.requests)["deviation"]
    payload["_deviation_sources"] = DEVIATION_SOURCES
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_benchmark(args) -> None:
    print(json.dumps(run_benchmark(args.services, args.edges),
                     ensure_ascii=False, indent=2))


def cmd_demo(args) -> None:
    os.makedirs(args.out, exist_ok=True)
    impact_data, deviation_data = {}, {}
    for name in sorted(SCENARIOS):
        topo, injected = SCENARIOS[name]()
        result = assess(topo, injected)
        impact_data[name] = {
            "injected_domains": sorted(injected),
            "summary": result.summary(),
            "detail": result.detail(),
            "critical_path_count": result.critical_path_count,
        }
        deviation_data[name] = compare_once(
            topo, injected, n_requests=args.requests)["deviation"]
    bench = run_benchmark()

    files = {
        "impact_data.json": impact_data,
        "deviation_data.json": {
            "scenarios": deviation_data,
            "deviation_sources": DEVIATION_SOURCES,
        },
        "benchmark.json": bench,
    }
    for fname, data in files.items():
        path = os.path.join(args.out, fname)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        print(f"wrote {path}")
    print(json.dumps(bench, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="blast",
                                     description="故障演练影响评估库")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scenarios", help="列出内置场景")
    p.set_defaults(func=cmd_scenarios)

    p = sub.add_parser("assess", help="对单个场景做影响评估")
    p.add_argument("name")
    p.set_defaults(func=cmd_assess)

    p = sub.add_parser("compare", help="评估结果与模拟演练对比")
    p.add_argument("name")
    p.add_argument("--requests", type=int, default=100_000)
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("compare-all", help="全部场景的偏差对比")
    p.add_argument("--requests", type=int, default=100_000)
    p.set_defaults(func=cmd_compare_all)

    p = sub.add_parser("benchmark", help="规模化耗时/内存基准")
    p.add_argument("--services", type=int, default=10_000)
    p.add_argument("--edges", type=int, default=100_000)
    p.set_defaults(func=cmd_benchmark)

    p = sub.add_parser("demo", help="生成全部交付数据文件")
    p.add_argument("--out", default="reports")
    p.add_argument("--requests", type=int, default=100_000)
    p.set_defaults(func=cmd_demo)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
