"""数据模型：调用图节点/边、漏洞输入、判定结果。"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Optional


# 边的种类（判定依据见 docs/CRITERIA.md）
EDGE_STATIC = "static"                      # 直接静态调用 foo() / mod.foo()
EDGE_METHOD_HEURISTIC = "method_heuristic"  # obj.foo()，按方法名保守匹配
EDGE_DYNAMIC_IMPORT = "dynamic_import"      # importlib.import_module("字面量")
EDGE_REFLECTION = "reflection"              # getattr(obj, "字面量")(...)
EDGE_CONFIG = "config"                      # 配置文件中的点分路径字符串

EDGE_KINDS = (
    EDGE_STATIC,
    EDGE_METHOD_HEURISTIC,
    EDGE_DYNAMIC_IMPORT,
    EDGE_REFLECTION,
    EDGE_CONFIG,
)


@dataclass(frozen=True)
class FunctionInfo:
    """一个可调用节点（函数 / 方法 / 模块顶层代码）。"""

    qualname: str          # 例: "pkg.mod.func" 或 "pkg.mod.<module>"
    module: str            # 所属模块点分名
    short_name: str        # 函数短名，模块体为 "<module>"
    filename: str
    lineno: int


@dataclass(frozen=True)
class Edge:
    """调用图中的一条边：caller -> callee，携带调用位置与守卫信息。"""

    caller: str            # caller 的 qualname
    callee: str            # callee 的 qualname
    kind: str              # EDGE_* 之一
    filename: str          # 调用点所在文件
    lineno: int            # 调用点行号
    blocked: bool = False          # 是否被可静态求值为假的条件挡住
    guard: Optional[str] = None    # 挡住它的条件源码片段（blocked=True 时非空）


@dataclass(frozen=True)
class DynamicNote:
    """无法静态解析的动态分发记录（非字面量 import_module / getattr 等）。"""

    function: str          # 所在的函数 qualname
    filename: str
    lineno: int
    expression: str        # 动态表达式源码片段
    mechanism: str         # "importlib.import_module" / "__import__" / "getattr"


@dataclass(frozen=True)
class Vulnerability:
    """漏洞输入：某个依赖的某些符号被认为存在漏洞。"""

    vuln_id: str
    dependency: str                    # 依赖名（与 requirements / 源码顶层包对应）
    vulnerable_symbols: tuple          # 点分符号名，如 ("requests.api.request",)
    advisory: str = ""


@dataclass
class Verdict:
    """对单个 (漏洞, 符号) 的判定结果。"""

    vuln_id: str
    dependency: str
    symbol: str
    status: str              # reachable / potentially_reachable / unreachable / dependency_missing
    reason: str              # 机器可读的理由码
    detail: str              # 人类可读说明
    path: list = field(default_factory=list)   # reachable 时的完整调用链（Edge 字典序列）
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)
