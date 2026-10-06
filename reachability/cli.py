"""命令行：扫描项目 + 依赖源码，输出可达/不可达判定 JSON。"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from typing import Dict, List, Tuple

from .analyzer import Analyzer, STATUS_REACHABLE
from .callgraph import build_graph
from .models import Vulnerability

DOTTED_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$")


def discover_python(root: str) -> List[Tuple[str, str, str]]:
    """递归收集目录下的 .py，返回 (模块点分名, 文件名, 源码)。"""
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            parts = rel[:-3].split(os.sep)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            if not parts:
                continue
            module = ".".join(parts)
            with open(full, encoding="utf-8") as handle:
                files.append((module, full, handle.read()))
    return files


def parse_requirements(path: str) -> List[str]:
    deps = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith(("#", "-", "/")):
                continue
            name = re.split(r"[<>=!~;\[\s]", line, 1)[0].strip()
            if name:
                deps.append(name)
    return deps


def load_vulnerabilities(path: str) -> List[Vulnerability]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    vulns = []
    for item in data:
        vulns.append(Vulnerability(
            vuln_id=item["id"],
            dependency=item["dependency"],
            vulnerable_symbols=tuple(item["symbols"]),
            advisory=item.get("advisory", ""),
        ))
    return vulns


def extract_config_strings(path: str) -> List[Tuple[str, str]]:
    """从 JSON 配置中提取点分路径字符串（插件/处理器名等）。

    这是配置驱动调用的保守近似：凡是形如 ``a.b.c`` 的字符串值，
    且能在调用图中解析到已知符号，才会真正成为 config 边。
    """
    results = []
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                walk(key)
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, str) and DOTTED_RE.match(node):
            results.append((path, node))

    walk(data)
    return results


def run(argv=None) -> int:
    parser = argparse.ArgumentParser(description="依赖漏洞可达性分析")
    parser.add_argument("--src", action="append", default=[],
                        help="应用源码目录（可重复）")
    parser.add_argument("--deps", action="append", default=[],
                        help="依赖源码目录（可重复，每个根作为包前缀）")
    parser.add_argument("--requirements", help="依赖清单文件")
    parser.add_argument("--vulns", required=True, help="漏洞清单 JSON")
    parser.add_argument("--entry", action="append", default=[],
                        help="显式入口符号 module.func（可重复）")
    parser.add_argument("--config", action="append", default=[],
                        help="配置文件 JSON（可重复）")
    parser.add_argument("--out", help="输出目录；不给则只打印摘要")
    args = parser.parse_args(argv)

    timings: Dict[str, float] = {}

    t0 = time.perf_counter()
    files: List[Tuple[str, str, str]] = []
    for root in args.src:
        files.extend(discover_python(root))
    for root in args.deps:
        files.extend(discover_python(root))
    config_strings: List[Tuple[str, str]] = []
    for path in args.config:
        config_strings.extend(extract_config_strings(path))
    timings["collect_files_seconds"] = round(time.perf_counter() - t0, 6)

    installed = parse_requirements(args.requirements) if args.requirements else []
    vulns = load_vulnerabilities(args.vulns)

    t0 = time.perf_counter()
    graph = build_graph(files, config_strings=config_strings)
    timings["build_graph_seconds"] = round(time.perf_counter() - t0, 6)

    t0 = time.perf_counter()
    analyzer = Analyzer(graph, vulns, installed_deps=installed,
                        entries=args.entry or None)
    result = analyzer.analyze()
    timings["analyze_seconds"] = round(time.perf_counter() - t0, 6)
    timings["total_seconds"] = round(
        sum(v for k, v in timings.items() if k != "total_seconds"), 6)

    payload = result.to_dict()
    payload["timings"] = timings

    reachable = [v for v in payload["verdicts"] if v["status"] == STATUS_REACHABLE]
    others = [v for v in payload["verdicts"] if v["status"] != STATUS_REACHABLE]

    print(f"调用点 {payload['stats']['call_sites']} 处，"
          f"函数 {payload['stats']['functions']} 个，"
          f"入口 {len(payload['stats']['entries'])} 个")
    counts: Dict[str, int] = {}
    for verdict in payload["verdicts"]:
        counts[verdict["status"]] = counts.get(verdict["status"], 0) + 1
    for status, count in sorted(counts.items()):
        print(f"  {status}: {count}")

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, "reachable.json"), "w",
                  encoding="utf-8") as handle:
            json.dump(reachable, handle, ensure_ascii=False, indent=2)
        with open(os.path.join(args.out, "unreachable.json"), "w",
                  encoding="utf-8") as handle:
            json.dump(others, handle, ensure_ascii=False, indent=2)
        with open(os.path.join(args.out, "summary.json"), "w",
                  encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        print(f"结果已写入 {args.out}/{{reachable,unreachable,summary}}.json")
    return 0


if __name__ == "__main__":
    sys.exit(run())
