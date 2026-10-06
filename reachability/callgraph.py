"""基于 ast 的静态调用图构建（仅标准库）。

解析规则（判定依据详见 docs/CRITERIA.md）：

* 直接调用 ``foo()`` / ``mod.foo()``：经 import 表与本地函数表解析；
* 方法调用 ``obj.foo()``：无法静态定型时按方法名做保守（过近似）匹配，
  边标记为 ``method_heuristic``；
* 动态导入 ``importlib.import_module("lit")`` / ``__import__("lit")``：
  字面量可解析则建边（``dynamic_import``），非字面量记入 DynamicNote；
* 反射 ``getattr(obj, "lit")(...)``：字面量按名字建边（``reflection``），
  非字面量记入 DynamicNote；
* 配置驱动：外部传入的 (来源, 点分路径) 字符串，能解析到已知函数/模块
  则建边（``config``），来源为合成入口 ``<config>``；
* 条件守卫：调用点被可静态求值为假的 ``if`` 包住时，边标记 blocked；
* 入口提示：名为 ``main`` 的函数、含 ``if __name__ == "__main__"`` 的
  模块体、以及 ``<config>`` 合成节点。
"""

from __future__ import annotations

import ast
from typing import Dict, Iterable, List, Optional, Tuple

from .conditions import collect_module_constants, eval_guard
from .models import (
    EDGE_CONFIG,
    EDGE_DYNAMIC_IMPORT,
    EDGE_METHOD_HEURISTIC,
    EDGE_REFLECTION,
    EDGE_STATIC,
    DynamicNote,
    Edge,
    FunctionInfo,
)

CONFIG_NODE = "<config>"          # 配置驱动调用的合成入口
MODULE_BODY = "<module>"          # 模块顶层代码的伪函数名


class CallGraph:
    """调用图：节点为函数 qualname，边为带位置信息的调用关系。"""

    def __init__(self) -> None:
        self.functions: Dict[str, FunctionInfo] = {}
        self.edges: List[Edge] = []
        self.dynamic_notes: List[DynamicNote] = []
        self.modules: set = set()
        self.by_caller: Dict[str, List[Edge]] = {}
        self.by_callee: Dict[str, List[Edge]] = {}
        self.name_index: Dict[str, List[str]] = {}
        self.entry_hints: List[str] = []

    def add_function(self, info: FunctionInfo) -> None:
        self.functions[info.qualname] = info
        self.name_index.setdefault(info.short_name, []).append(info.qualname)

    def add_edge(self, edge: Edge) -> None:
        self.edges.append(edge)
        self.by_caller.setdefault(edge.caller, []).append(edge)
        self.by_callee.setdefault(edge.callee, []).append(edge)

    @property
    def call_site_count(self) -> int:
        return len(self.edges)

    def finalize(self) -> None:
        seen = set()
        unique: List[Edge] = []
        for edge in self.edges:
            key = (edge.caller, edge.callee, edge.kind, edge.filename, edge.lineno)
            if key not in seen:
                seen.add(key)
                unique.append(edge)
        self.edges = unique
        self.by_caller = {}
        self.by_callee = {}
        for edge in self.edges:
            self.by_caller.setdefault(edge.caller, []).append(edge)
            self.by_callee.setdefault(edge.callee, []).append(edge)


# ---------------------------------------------------------------------------
# 原始调用点（第一遍扫描记录，第二遍统一解析）
# ---------------------------------------------------------------------------

class _RawCall:
    __slots__ = ("module", "owner", "kind", "parts", "head", "attr_tail",
                 "literal", "lineno", "blocked", "guard", "expr", "mechanism")

    def __init__(self, **kw):
        for key, value in kw.items():
            setattr(self, key, value)


class _ModuleContext:
    """单个模块的扫描上下文。"""

    def __init__(self, graph: CallGraph, module: str, filename: str, source: str):
        self.graph = graph
        self.module = module
        self.filename = filename
        self.source = source
        self.imports: Dict[str, str] = {}
        self.owner_imports: Dict[str, Dict[str, str]] = {}
        self.aliases: Dict[str, Dict[str, tuple]] = {}
        self.constants: Dict[str, object] = {}
        self.raw_calls: List[_RawCall] = []

    # -- 第一遍：注册结构 ------------------------------------------------
    def register(self) -> None:
        tree = ast.parse(self.source, filename=self.filename)
        self.graph.modules.add(self.module)
        self._collect_imports(tree)
        self.constants = collect_module_constants(tree)
        body_qual = f"{self.module}.{MODULE_BODY}"
        self.graph.add_function(FunctionInfo(
            qualname=body_qual, module=self.module,
            short_name=MODULE_BODY, filename=self.filename, lineno=1,
        ))
        if self._has_main_guard(tree):
            self.graph.entry_hints.append(body_qual)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._register_function(node, f"{self.module}.{node.name}",
                                        node.name, node.lineno)
            elif isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        self._register_function(
                            sub, f"{self.module}.{node.name}.{sub.name}",
                            sub.name, sub.lineno)
        self._tree = tree

    def _register_function(self, node, qual: str, short: str, lineno: int) -> None:
        self.graph.add_function(FunctionInfo(
            qualname=qual, module=self.module, short_name=short,
            filename=self.filename, lineno=lineno,
        ))
        if short == "main":
            self.graph.entry_hints.append(qual)

    @staticmethod
    def _has_main_guard(tree: ast.Module) -> bool:
        for node in tree.body:
            if isinstance(node, ast.If) and _is_main_guard_test(node.test):
                return True
        return False

    def _collect_imports(self, tree: ast.Module) -> None:
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        self.imports[alias.asname] = alias.name
                    else:
                        # `import a.b` 绑定的是顶层名 a，a.b 模块本身另记
                        self.imports[alias.name.split(".")[0]] = alias.name.split(".")[0]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                for alias in node.names:
                    self.imports[alias.asname or alias.name] = f"{node.module}.{alias.name}"

    # -- 第一遍续：记录原始调用点 ----------------------------------------
    def scan_calls(self) -> None:
        tree = self._tree
        body_qual = f"{self.module}.{MODULE_BODY}"
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._scan_block(node.body, f"{self.module}.{node.name}", False, None)
            elif isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        qual = f"{self.module}.{node.name}.{sub.name}"
                        self._scan_block(sub.body, qual, False, None)
            else:
                self._scan_stmt(node, body_qual, False, None)

    def _scan_block(self, stmts, owner: str, blocked: bool, guard: Optional[str]) -> None:
        for stmt in stmts:
            self._scan_stmt(stmt, owner, blocked, guard)

    def _scan_stmt(self, node: ast.AST, owner: str, blocked: bool,
                   guard: Optional[str]) -> None:
        if isinstance(node, ast.If):
            self._scan_stmt(node.test, owner, blocked, guard)
            value = eval_guard(node.test, self.constants)
            segment = ast.get_source_segment(self.source, node.test) or "?"
            if value is False:
                self._scan_block(node.body, owner, True, segment)
                self._scan_block(node.orelse, owner, blocked, guard)
            elif value is True:
                self._scan_block(node.body, owner, blocked, guard)
                self._scan_block(node.orelse, owner, True, f"not ({segment})")
            else:
                self._scan_block(node.body, owner, blocked, guard)
                self._scan_block(node.orelse, owner, blocked, guard)
            return
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            self._record_owner_import(node, owner)
            return
        if isinstance(node, ast.Assign):
            self._record_alias(node, owner)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef, ast.Lambda)):
            return  # 嵌套作用域不属于当前 owner
        if isinstance(node, ast.Call):
            self._record_call(node, owner, blocked, guard)
        for child in ast.iter_child_nodes(node):
            self._scan_stmt(child, owner, blocked, guard)

    def _record_owner_import(self, node, owner: str) -> None:
        table = self.owner_imports.setdefault(owner, {})
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    table[alias.asname] = alias.name
                else:
                    table[alias.name.split(".")[0]] = alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                table[alias.asname or alias.name] = f"{node.module}.{alias.name}"

    def _record_alias(self, node: ast.Assign, owner: str) -> None:
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            return
        name = node.targets[0].id
        value = node.value
        if isinstance(value, ast.Call):
            func = value.func
            is_getattr = isinstance(func, ast.Name) and func.id == "getattr"
            is_import = False
            if isinstance(func, ast.Name) and func.id == "__import__":
                is_import = True
            elif isinstance(func, ast.Attribute) and func.attr == "import_module":
                if isinstance(func.value, ast.Name) and \
                        self.imports.get(func.value.id) == "importlib":
                    is_import = True
            if is_getattr and len(value.args) >= 2:
                name_arg = value.args[1]
                if isinstance(name_arg, ast.Constant) and isinstance(name_arg.value, str):
                    self.aliases.setdefault(owner, {})[name] = ("getattr", name_arg.value)
            elif is_import and value.args and isinstance(value.args[0], ast.Constant) \
                    and isinstance(value.args[0].value, str):
                self.aliases.setdefault(owner, {})[name] = ("import", value.args[0].value)

    def _record_call(self, call: ast.Call, owner: str, blocked: bool,
                     guard: Optional[str]) -> None:
        # 动态导入
        dyn = self._dynamic_import_target(call)
        if dyn is not None:
            literal, expr = dyn
            self.raw_calls.append(_RawCall(
                module=self.module, owner=owner, kind="dyn_import",
                parts=None, head=None, attr_tail=None, literal=literal,
                lineno=call.lineno, blocked=blocked, guard=guard,
                expr=expr, mechanism="importlib.import_module"))
            return
        # 反射调用 getattr(obj, "name")(...)
        if isinstance(call.func, ast.Call) and _is_getattr(call.func):
            name_arg = call.func.args[1]
            if isinstance(name_arg, ast.Constant) and isinstance(name_arg.value, str):
                literal, expr = name_arg.value, None
            else:
                literal = None
                expr = ast.get_source_segment(self.source, call) or "getattr(...)"
            self.raw_calls.append(_RawCall(
                module=self.module, owner=owner, kind="getattr",
                parts=None, head=None, attr_tail=None, literal=literal,
                lineno=call.lineno, blocked=blocked, guard=guard,
                expr=expr, mechanism="getattr"))
            return
        # 普通调用：拆成点分路径
        parts = _dotted_parts(call.func)
        if parts:
            self.raw_calls.append(_RawCall(
                module=self.module, owner=owner, kind="call",
                parts=parts, head=parts[0],
                attr_tail=parts[-1] if len(parts) > 1 else None,
                literal=None, lineno=call.lineno, blocked=blocked,
                guard=guard, expr=None, mechanism=None))
        elif isinstance(call.func, ast.Attribute):
            # 形如 foo().bar()：接收者表达式无法静态定型，按方法名保守匹配
            self.raw_calls.append(_RawCall(
                module=self.module, owner=owner, kind="call",
                parts=None, head=None, attr_tail=call.func.attr,
                literal=None, lineno=call.lineno, blocked=blocked,
                guard=guard, expr=None, mechanism=None))

    def _dynamic_import_target(self, call: ast.Call) -> Optional[Tuple[Optional[str], Optional[str]]]:
        func = call.func
        is_import_call = False
        if isinstance(func, ast.Name) and func.id == "__import__":
            is_import_call = True
        elif isinstance(func, ast.Attribute) and func.attr == "import_module":
            value = func.value
            if isinstance(value, ast.Name) and self.imports.get(value.id) == "importlib":
                is_import_call = True
        elif isinstance(func, ast.Name) and func.id == "import_module":
            if self.imports.get("import_module") == "importlib.import_module":
                is_import_call = True
        if not is_import_call or not call.args:
            return None
        first = call.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return (first.value, None)
        return (None, ast.get_source_segment(self.source, first) or "<expr>")


def _is_getattr(call: ast.Call) -> bool:
    return (isinstance(call.func, ast.Name) and call.func.id == "getattr"
            and len(call.args) >= 2)


def _is_main_guard_test(test: ast.AST) -> bool:
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq)):
        return False
    if not (isinstance(test.left, ast.Name) and test.left.id == "__name__"):
        return False
    comp = test.comparators[0]
    return isinstance(comp, ast.Constant) and comp.value == "__main__"


def _dotted_parts(func: ast.expr) -> Optional[List[str]]:
    parts = []
    node = func
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        parts.reverse()
        return parts
    return None


# ---------------------------------------------------------------------------
# 第二遍：解析原始调用点为边
# ---------------------------------------------------------------------------

class _Resolver:
    def __init__(self, graph: CallGraph):
        self.graph = graph

    def resolve(self, ctx: _ModuleContext, raw: _RawCall) -> None:
        if raw.kind == "dyn_import":
            self._resolve_dyn_import(ctx, raw)
        elif raw.kind == "getattr":
            self._resolve_getattr(ctx, raw)
        else:
            self._resolve_plain(ctx, raw)

    def _emit(self, raw: _RawCall, callee: str, kind: str, filename: str) -> None:
        if callee not in self.graph.functions:
            return
        self.graph.add_edge(Edge(
            caller=raw.owner, callee=callee, kind=kind,
            filename=filename, lineno=raw.lineno,
            blocked=raw.blocked, guard=raw.guard if raw.blocked else None,
        ))

    def _resolve_dyn_import(self, ctx: _ModuleContext, raw: _RawCall) -> None:
        if raw.literal is None:
            self.graph.dynamic_notes.append(DynamicNote(
                function=raw.owner, filename=ctx.filename, lineno=raw.lineno,
                expression=raw.expr or "<expr>", mechanism=raw.mechanism))
            return
        target = self._longest_prefix(raw.literal)
        if target:
            self._emit(raw, target, EDGE_DYNAMIC_IMPORT, ctx.filename)

    def _resolve_getattr(self, ctx: _ModuleContext, raw: _RawCall) -> None:
        if raw.literal is None:
            self.graph.dynamic_notes.append(DynamicNote(
                function=raw.owner, filename=ctx.filename, lineno=raw.lineno,
                expression=raw.expr or "getattr(...)", mechanism="getattr"))
            return
        for qual in self.graph.name_index.get(raw.literal, []):
            self._emit(raw, qual, EDGE_REFLECTION, ctx.filename)

    def _resolve_plain(self, ctx: _ModuleContext, raw: _RawCall) -> None:
        if raw.parts is None:
            # 接收者不可定型（如 foo().bar()），仅按方法名保守匹配
            if raw.attr_tail is not None:
                for qual in self.graph.name_index.get(raw.attr_tail, []):
                    self._emit(raw, qual, EDGE_METHOD_HEURISTIC, ctx.filename)
            return
        dotted = ".".join(raw.parts)
        # 0) 函数内别名：x = getattr(obj, "lit") / x = import_module("lit")
        alias = ctx.aliases.get(raw.owner, {}).get(raw.head)
        if alias is not None:
            kind, literal = alias
            if kind == "getattr" and len(raw.parts) == 1:
                for qual in self.graph.name_index.get(literal, []):
                    self._emit(raw, qual, EDGE_REFLECTION, ctx.filename)
                return
            if kind == "import":
                expanded = literal + dotted[len(raw.head):]
                hit = self._longest_prefix(expanded)
                if hit:
                    self._emit(raw, hit, EDGE_DYNAMIC_IMPORT, ctx.filename)
                return
        # 1) 局部 import，再模块级 import
        for import_map in (ctx.owner_imports.get(raw.owner, {}), ctx.imports):
            if raw.head in import_map:
                expanded = import_map[raw.head] + dotted[len(raw.head):]
                hit = self._longest_prefix(expanded)
                if hit:
                    self._emit(raw, hit, EDGE_STATIC, ctx.filename)
                return
        # 2) 本模块内解析
        hit = self._longest_prefix(f"{ctx.module}.{dotted}")
        if hit:
            self._emit(raw, hit, EDGE_STATIC, ctx.filename)
            return
        # 3) obj.method()：按方法名保守匹配（过近似）
        if raw.attr_tail is not None:
            for qual in self.graph.name_index.get(raw.attr_tail, []):
                self._emit(raw, qual, EDGE_METHOD_HEURISTIC, ctx.filename)

    def _longest_prefix(self, dotted: str) -> Optional[str]:
        candidate = dotted
        while candidate:
            if candidate in self.graph.functions:
                return candidate
            if candidate in self.graph.modules:
                return f"{candidate}.{MODULE_BODY}"
            candidate, _, _ = candidate.rpartition(".")
        return None


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------

def build_graph(files: Iterable[Tuple[str, str, str]],
                config_strings: Iterable[Tuple[str, str]] = ()) -> CallGraph:
    """构建调用图。

    :param files: ``(模块点分名, 文件名, 源码)`` 三元组，应用与依赖源码均可。
    :param config_strings: ``(来源描述, 点分路径字符串)``，来自配置文件的
        类名/函数路径，用于识别配置驱动调用。
    """
    graph = CallGraph()
    contexts: List[_ModuleContext] = []
    for module, filename, source in files:
        ctx = _ModuleContext(graph, module, filename, source)
        ctx.register()
        contexts.append(ctx)
    for ctx in contexts:
        ctx.scan_calls()
    resolver = _Resolver(graph)
    for ctx in contexts:
        for raw in ctx.raw_calls:
            resolver.resolve(ctx, raw)
    _add_config_edges(graph, config_strings)
    graph.finalize()
    return graph


def _add_config_edges(graph: CallGraph,
                      config_strings: Iterable[Tuple[str, str]]) -> None:
    resolver = _Resolver(graph)
    added = False
    for origin, dotted in config_strings:
        target = resolver._longest_prefix(dotted)
        if not target:
            continue
        if not added:
            graph.add_function(FunctionInfo(
                qualname=CONFIG_NODE, module=CONFIG_NODE, short_name=CONFIG_NODE,
                filename=origin, lineno=1,
            ))
            graph.entry_hints.append(CONFIG_NODE)
            added = True
        graph.add_edge(Edge(
            caller=CONFIG_NODE, callee=target, kind=EDGE_CONFIG,
            filename=origin, lineno=1,
        ))
