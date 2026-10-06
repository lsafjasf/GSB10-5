"""可达性判定器。

判定逻辑（依据详见 docs/CRITERIA.md）：

1. 从入口集合出发，沿未被条件挡死的边做 BFS，得到可达集合；
2. 再沿全部边（含被挡死的）做 BFS，用于识别“仅被条件挡死”的路径；
3. 对每个漏洞符号按优先级判定：
   - dependency_missing  依赖既不在清单中，也没有提供源码；
   - unknown             依赖已安装但未提供源码，无法静态判定；
   - reachable           存在从入口到符号的完整调用链（逐跳给出）；
   - potentially_reachable 静态不可达，但可达函数中存在无法解析的
                         动态分发（非字面量 import_module / getattr）；
   - unreachable         给出具体理由：symbol_not_found /
                         never_referenced / no_path_from_entry /
                         blocked_by_condition。
"""

from __future__ import annotations

import time
from collections import deque
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .callgraph import CONFIG_NODE, CallGraph
from .models import Vulnerability, Verdict

STATUS_REACHABLE = "reachable"
STATUS_POTENTIAL = "potentially_reachable"
STATUS_UNREACHABLE = "unreachable"
STATUS_DEP_MISSING = "dependency_missing"
STATUS_UNKNOWN = "unknown"


class AnalysisResult:
    def __init__(self, verdicts: List[Verdict], stats: dict):
        self.verdicts = verdicts
        self.stats = stats

    @property
    def reachable(self) -> List[Verdict]:
        return [v for v in self.verdicts if v.status == STATUS_REACHABLE]

    @property
    def unreachable(self) -> List[Verdict]:
        return [v for v in self.verdicts if v.status in (
            STATUS_UNREACHABLE, STATUS_DEP_MISSING, STATUS_UNKNOWN)]

    def to_dict(self) -> dict:
        return {
            "stats": self.stats,
            "verdicts": [v.to_dict() for v in self.verdicts],
        }


class Analyzer:
    def __init__(self,
                 graph: CallGraph,
                 vulnerabilities: Sequence[Vulnerability],
                 installed_deps: Iterable[str] = (),
                 entries: Optional[Sequence[str]] = None):
        self.graph = graph
        self.vulnerabilities = list(vulnerabilities)
        self.installed_deps = {d.strip().lower() for d in installed_deps if d.strip()}
        if entries is None:
            entries = list(graph.entry_hints)
        self.entries = [e for e in entries if e in graph.functions]
        self.missing_entries = [e for e in entries if e not in graph.functions]

    # -- BFS -------------------------------------------------------------
    def _bfs(self, include_blocked: bool) -> Tuple[Set[str], Dict[str, object]]:
        visited: Set[str] = set(self.entries)
        parent: Dict[str, object] = {}
        queue = deque(self.entries)
        by_caller = self.graph.by_caller
        while queue:
            node = queue.popleft()
            for edge in by_caller.get(node, ()):
                if edge.blocked and not include_blocked:
                    continue
                if edge.callee not in visited:
                    visited.add(edge.callee)
                    parent[edge.callee] = edge
                    queue.append(edge.callee)
        return visited, parent

    @staticmethod
    def _reconstruct(symbol: str, parent: Dict[str, object],
                     entries: Set[str]) -> List[dict]:
        """从 symbol 沿父边回溯到入口，返回正序的逐跳调用链。"""
        hops = []
        node = symbol
        guard = 0
        while node not in entries and node in parent and guard <= len(parent) + 1:
            edge = parent[node]
            hops.append({
                "from": edge.caller,
                "to": edge.callee,
                "kind": edge.kind,
                "file": edge.filename,
                "line": edge.lineno,
            })
            node = edge.caller
            guard += 1
        hops.reverse()
        return hops

    # -- 依赖存在性 --------------------------------------------------------
    def _dep_present_in_sources(self, dep: str) -> bool:
        prefix = dep + "."
        return any(m == dep or m.startswith(prefix) for m in self.graph.modules)

    def _dep_installed(self, dep: str) -> bool:
        return dep.lower() in self.installed_deps

    # -- 主流程 ------------------------------------------------------------
    def analyze(self) -> AnalysisResult:
        started = time.perf_counter()
        reachable, parent = self._bfs(include_blocked=False)
        reachable_all, parent_all = self._bfs(include_blocked=True)
        entries_set = set(self.entries)

        # 可达函数中未解析的动态分发记录
        live_notes = [n for n in self.graph.dynamic_notes
                      if n.function in reachable]

        verdicts: List[Verdict] = []
        for vuln in self.vulnerabilities:
            for symbol in vuln.vulnerable_symbols:
                verdicts.append(self._judge(
                    vuln, symbol, reachable, parent, reachable_all,
                    parent_all, entries_set, live_notes))

        stats = {
            "entries": sorted(entries_set),
            "missing_entries": self.missing_entries,
            "functions": len(self.graph.functions),
            "call_sites": self.graph.call_site_count,
            "reachable_functions": len(reachable),
            "dynamic_notes_unresolved": len(self.graph.dynamic_notes),
            "analysis_seconds": round(time.perf_counter() - started, 6),
        }
        return AnalysisResult(verdicts, stats)

    # -- 单符号判定 --------------------------------------------------------
    def _judge(self, vuln: Vulnerability, symbol: str,
               reachable: Set[str], parent: Dict[str, object],
               reachable_all: Set[str], parent_all: Dict[str, object],
               entries_set: Set[str], live_notes: list) -> Verdict:
        dep = vuln.dependency
        base = {"vuln_id": vuln.vuln_id, "dependency": dep, "symbol": symbol}

        in_sources = self._dep_present_in_sources(dep)
        installed = self._dep_installed(dep)

        if not in_sources and not installed:
            return Verdict(
                **base, status=STATUS_DEP_MISSING,
                reason="dependency_not_installed",
                detail=(f"依赖 {dep} 不在依赖清单中，也未提供其源码；"
                        f"项目未携带该依赖，漏洞符号 {symbol} 不可能被调用。"))

        if not in_sources and installed:
            return Verdict(
                **base, status=STATUS_UNKNOWN,
                reason="dependency_sources_not_provided",
                detail=(f"依赖 {dep} 在清单中但未提供源码，无法静态判定；"
                        f"请用 --deps 提供其源码目录。"))

        if symbol not in self.graph.functions:
            return Verdict(
                **base, status=STATUS_UNREACHABLE,
                reason="symbol_not_found",
                detail=(f"依赖 {dep} 的源码中不存在符号 {symbol}"
                        f"（可能版本不含该符号或名称有误）。"))

        if symbol in reachable:
            path = self._reconstruct(symbol, parent, entries_set)
            kinds = sorted({h["kind"] for h in path})
            return Verdict(
                **base, status=STATUS_REACHABLE,
                reason="path_from_entry_exists",
                detail=(f"存在从入口到 {symbol} 的完整调用链"
                        f"（{len(path)} 跳，经过的边类型: {', '.join(kinds)}）。"),
                path=path)

        if symbol in reachable_all:
            # 仅被条件挡死：找出链上第一条 blocked 边作为证据
            chain = self._reconstruct(symbol, parent_all, entries_set)
            blockers = []
            node = symbol
            seen = 0
            while node not in entries_set and node in parent_all and seen <= len(parent_all) + 1:
                edge = parent_all[node]
                if edge.blocked:
                    blockers.append({
                        "file": edge.filename, "line": edge.lineno,
                        "guard": edge.guard, "caller": edge.caller,
                    })
                node = edge.caller
                seen += 1
            return Verdict(
                **base, status=STATUS_UNREACHABLE,
                reason="blocked_by_condition",
                detail=(f"从入口到 {symbol} 的全部路径都经过可静态求值为假的"
                        f"条件分支，调用被条件挡死。"),
                evidence={"blocked_path": chain, "blockers": blockers})

        if self.graph.by_callee.get(symbol):
            return Verdict(
                **base, status=STATUS_UNREACHABLE,
                reason="no_path_from_entry",
                detail=(f"符号 {symbol} 被项目内代码引用，但引用点本身"
                        f"不在任何入口的可达闭包内。"),
                evidence={"referenced_from": sorted({
                    e.caller for e in self.graph.by_callee[symbol]})})

        if live_notes:
            return Verdict(
                **base, status=STATUS_POTENTIAL,
                reason="unresolved_dynamic_dispatch",
                detail=(f"符号 {symbol} 未被静态引用，但可达代码中存在无法"
                        f"静态解析的动态分发，不能排除运行时触达。"),
                evidence={"dynamic_notes": [
                    {"function": n.function, "file": n.filename,
                     "line": n.lineno, "expression": n.expression,
                     "mechanism": n.mechanism} for n in live_notes]})

        return Verdict(
            **base, status=STATUS_UNREACHABLE,
            reason="never_referenced",
            detail=(f"符号 {symbol} 在项目与依赖源码中从未被任何调用点"
                    f"引用（无入边）。"))
