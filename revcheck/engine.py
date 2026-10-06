"""检查引擎：解析源码、建立跨文件索引、运行规则。"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from .model import Finding
from .rules import ALL_RULES, FileContext, build_structure_map


@dataclass
class ProjectIndex:
    """跨文件上下文：模块名 -> 顶层定义的名字集合。"""

    modules: Dict[str, Set[str]] = field(default_factory=dict)

    @classmethod
    def from_tree(cls, index: "ProjectIndex", module_name: str, tree: ast.AST) -> None:
        names: Set[str] = set()
        for node in getattr(tree, "body", []):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    names.update(_target_names(target))
            elif isinstance(node, ast.AnnAssign):
                names.update(_target_names(node.target))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name != "*":
                        names.add(alias.asname or alias.name)
        index.modules[module_name] = names


def _target_names(target: ast.AST) -> Set[str]:
    names: Set[str] = set()
    if isinstance(target, ast.Name):
        names.add(target.id)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            names.update(_target_names(elt))
    return names


def module_name_for(path: str, root: Optional[str] = None) -> str:
    """文件路径 -> 模块名 (取去扩展名的文件名；包内文件取最后一段)。"""
    rel = os.path.relpath(path, root) if root else path
    parts = os.path.splitext(rel)[0].split(os.sep)
    return parts[-1]


def check_source(
    source: str,
    path: str = "<string>",
    index: Optional[ProjectIndex] = None,
) -> List[Finding]:
    """检查一段源码，返回报警列表 (按行号排序)。"""
    tree = ast.parse(source, filename=path)
    ctx = FileContext(
        path=path,
        module_name=module_name_for(path),
        index=index,
        structures=build_structure_map(tree),
    )
    findings: List[Finding] = []
    for rule in ALL_RULES:
        findings.extend(rule(tree, ctx))
    return _dedupe(findings)


def check_file(path: str, index: Optional[ProjectIndex] = None) -> List[Finding]:
    with open(path, "r", encoding="utf-8") as fh:
        source = fh.read()
    return check_source(source, path=path, index=index)


def iter_python_files(root: str):
    if os.path.isfile(root):
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def check_project(root: str) -> List[Finding]:
    """两遍扫描：先建跨文件索引，再逐文件检查。"""
    files = list(iter_python_files(root))
    index = ProjectIndex()
    trees: Dict[str, ast.AST] = {}
    for path in files:
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError:
            continue
        ProjectIndex.from_tree(index, module_name_for(path, root), tree)
        trees[path] = tree

    findings: List[Finding] = []
    for path in files:
        tree = trees.pop(path, None)
        if tree is None:
            continue
        ctx = FileContext(
            path=path,
            module_name=module_name_for(path, root),
            index=index,
            structures=build_structure_map(tree),
        )
        for rule in ALL_RULES:
            findings.extend(rule(tree, ctx))
    return _dedupe(findings)


def _dedupe(findings: List[Finding]) -> List[Finding]:
    seen = set()
    out: List[Finding] = []
    for f in sorted(findings, key=lambda f: (f.path, f.line, f.col, f.rule_id)):
        key = (f.rule_id, f.path, f.line, f.col)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out
