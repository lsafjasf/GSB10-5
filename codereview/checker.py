"""基于语法树（ast）的代码评审检查器。

所有规则都在 AST 节点层面判定，因此字符串常量、注释、文档字符串中的
相似文本天然不会被当作代码检查，也不会被算作“使用”。
"""
from __future__ import annotations

import ast
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    """一条报警：文件、行号、列、所在结构、规则编号与说明。"""

    rule_id: str
    path: str
    line: int
    col: int
    structure: str
    message: str

    def format(self) -> str:
        return f"{self.path}:{self.line}:{self.col}: {self.rule_id} [{self.structure}] {self.message}"


MUTABLE_DEFAULT_CALLS = {"list", "dict", "set"}
DANGEROUS_CALLS = {"eval", "exec"}
SQL_EXECUTE_ATTRS = {"execute", "executemany", "executescript"}


def _is_mutable_default(node: ast.AST) -> bool:
    if isinstance(node, (ast.List, ast.Dict, ast.Set)):
        return True
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in MUTABLE_DEFAULT_CALLS
    )


def _is_dynamic_sql(node: ast.AST) -> bool:
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return True
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "format"
    )


class _RuleVisitor(ast.NodeVisitor):
    """实现 CR001~CR005（单文件内规则）并收集 CR004/CR006 所需信息。"""

    def __init__(self, path: str):
        self.path = path
        self.findings: list[Finding] = []
        self._scope: list[str] = []
        self.import_records: list[tuple[ast.stmt, ast.alias, str]] = []
        self.import_from_nodes: list[ast.ImportFrom] = []

    @property
    def structure(self) -> str:
        return ".".join(self._scope) if self._scope else "<module>"

    def _emit(self, rule_id: str, node: ast.AST, message: str) -> None:
        self.findings.append(
            Finding(
                rule_id=rule_id,
                path=self.path,
                line=node.lineno,
                col=node.col_offset,
                structure=self.structure,
                message=message,
            )
        )

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node) -> None:
        self._scope.append(node.name)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None and _is_mutable_default(default):
                self._emit(
                    "CR001",
                    default,
                    f"使用可变默认参数 {ast.unparse(default)}，默认对象在所有调用间共享",
                )
        self.generic_visit(node)
        self._scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is None:
            self._emit("CR002", node, "裸 except 会吞掉 KeyboardInterrupt/SystemExit 等所有异常")
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            bound = alias.asname or alias.name.split(".")[0]
            self.import_records.append((node, alias, bound))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.import_from_nodes.append(node)
        for alias in node.names:
            if alias.name != "*":
                bound = alias.asname or alias.name
                self.import_records.append((node, alias, bound))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name) and func.id in DANGEROUS_CALLS:
            self._emit("CR003", node, f"禁止直接调用 {func.id}()，存在代码注入风险")
        elif (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "os"
            and func.attr == "system"
        ):
            self._emit("CR003", node, "禁止直接调用 os.system()，存在命令注入风险")
        elif (
            isinstance(func, ast.Attribute)
            and func.attr in SQL_EXECUTE_ATTRS
            and node.args
            and _is_dynamic_sql(node.args[0])
        ):
            self._emit("CR005", node, "SQL 语句通过 f-string/%/.format 拼接，存在 SQL 注入风险")
        self.generic_visit(node)

    def check_unused_imports(self, tree: ast.AST) -> None:
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
                and isinstance(node.value, (ast.List, ast.Tuple))
            ):
                used.update(
                    el.value
                    for el in node.value.elts
                    if isinstance(el, ast.Constant) and isinstance(el.value, str)
                )
        for stmt, alias, bound in self.import_records:
            if bound not in used:
                line = getattr(alias, "lineno", stmt.lineno)
                col = getattr(alias, "col_offset", stmt.col_offset)
                self.findings.append(
                    Finding(
                        "CR004",
                        self.path,
                        line,
                        col,
                        "<module>",
                        f"导入的 {alias.name} 在本模块未被使用",
                    )
                )


def _bound_names(node: ast.stmt) -> set[str]:
    names: set[str] = set()
    if isinstance(node, ast.Import):
        for alias in node.names:
            names.add(alias.asname or alias.name.split(".")[0])
    elif isinstance(node, ast.ImportFrom):
        for alias in node.names:
            if alias.name != "*":
                names.add(alias.asname or alias.name)
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        names.add(node.name)
    elif isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        names.add(node.target.id)
    return names


def _collect_py_files(paths: list[str]) -> list[str]:
    files: list[str] = []
    for path in paths:
        if os.path.isdir(path):
            for dirpath, dirnames, filenames in os.walk(path):
                dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
                for name in sorted(filenames):
                    if name.endswith(".py"):
                        files.append(os.path.join(dirpath, name))
        elif path.endswith(".py"):
            files.append(path)
    return files


def _module_name(path: str, root: str) -> str:
    rel = os.path.relpath(path, root)
    parts = rel[:-3].split(os.sep)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve_relative(current_module: str, is_package: bool, node: ast.ImportFrom) -> str:
    parts = current_module.split(".") if current_module else []
    pkg = parts if is_package else parts[:-1]
    if node.level > 0:
        pkg = pkg[: len(pkg) - (node.level - 1)]
    if node.module:
        pkg = pkg + node.module.split(".")
    return ".".join(p for p in pkg if p)


def check_source(
    source: str,
    path: str = "<memory>",
    module_index: dict[str, set[str]] | None = None,
    module_name: str = "",
    is_package: bool = False,
) -> list[Finding]:
    """检查单个源码字符串，返回 Finding 列表。"""
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return [
            Finding(
                "CR000",
                path,
                exc.lineno or 1,
                exc.offset or 0,
                "<module>",
                f"语法解析失败：{exc.msg}",
            )
        ]

    visitor = _RuleVisitor(path)
    visitor.visit(tree)
    visitor.check_unused_imports(tree)

    if module_index is not None:
        for node in visitor.import_from_nodes:
            target = (
                node.module or ""
                if node.level == 0
                else _resolve_relative(module_name, is_package, node)
            )
            if target not in module_index:
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                if alias.name in module_index[target]:
                    continue
                if f"{target}.{alias.name}" in module_index:
                    continue
                line = getattr(alias, "lineno", node.lineno)
                col = getattr(alias, "col_offset", node.col_offset)
                visitor.findings.append(
                    Finding(
                        "CR006",
                        path,
                        line,
                        col,
                        "<module>",
                        f"本地模块 {target} 未定义符号 {alias.name}（疑似失效导入）",
                    )
                )

    return sorted(visitor.findings, key=lambda f: (f.path, f.line, f.col, f.rule_id))


def check_paths(paths: list[str]) -> list[Finding]:
    """检查若干文件/目录，返回全部 Finding（按文件、行号排序）。"""
    files = [os.path.abspath(p) for p in _collect_py_files(paths)]
    if not files:
        return []
    root = os.path.commonpath(files)
    if os.path.isfile(root):
        root = os.path.dirname(root)

    trees: dict[str, ast.Module] = {}
    modules: dict[str, str] = {}
    for path in files:
        module = _module_name(path, root)
        try:
            with open(path, encoding="utf-8") as fh:
                trees[path] = ast.parse(fh.read(), filename=path)
            modules[path] = module
        except SyntaxError:
            pass

    module_index: dict[str, set[str]] = {}
    for path, tree in trees.items():
        symbols: set[str] = set()
        for node in tree.body:
            symbols |= _bound_names(node)
        module_index[modules[path]] = symbols

    findings: list[Finding] = []
    for path in files:
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        is_package = os.path.basename(path) == "__init__.py"
        findings.extend(
            check_source(
                source,
                path=path,
                module_index=module_index,
                module_name=modules.get(path, ""),
                is_package=is_package,
            )
        )
    return sorted(findings, key=lambda f: (f.path, f.line, f.col, f.rule_id))
