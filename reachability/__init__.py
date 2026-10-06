"""依赖漏洞可达性分析库（仅标准库）。

公开接口见 :class:`reachability.analyzer.Analyzer` 与
:func:`reachability.callgraph.build_graph`。
"""

from .models import Edge, FunctionInfo, Vulnerability, Verdict
from .callgraph import CallGraph, build_graph
from .analyzer import AnalysisResult, Analyzer

__all__ = [
    "Edge",
    "FunctionInfo",
    "Vulnerability",
    "Verdict",
    "CallGraph",
    "build_graph",
    "Analyzer",
    "AnalysisResult",
]
