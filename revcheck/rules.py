"""检查规则集合。

每条规则只检查 ast 语法树中的特定节点类型，不做任何文本/正则匹配。
规则注册表 RULES 同时是规则文档的单一事实来源 (rule id / 标题 /
依据 / 命中节点)。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set

from .model import Finding

MUTABLE_FACTORY_CALLS = {"list", "dict", "set"}


def _is_sentinel_constant(node: ast.AST) -> bool:
    # 必须用身份比较: 1 == True / 0 == False 会让集合成员判断误命中
    return (
        isinstance(node, ast.Constant)
        and (node.value is None or node.value is True or node.value is False)
    )
DANGEROUS_BUILTINS = {"eval", "exec"}
TERMINATORS = (ast.Return, ast.Raise, ast.Continue, ast.Break)


@dataclass
class RuleMeta:
    rule_id: str
    title: str
    rationale: str
    ast_basis: str


RULES: Dict[str, RuleMeta] = {
    "RV001": RuleMeta(
        "RV001",
        "可变默认参数",
        "默认参数在函数定义时只求值一次，可变对象会在多次调用之间共享状态。",
        "FunctionDef/AsyncFunctionDef 的 args.defaults / args.kw_defaults "
        "中的 List/Dict/Set 字面量，或 list()/dict()/set() 调用。",
    ),
    "RV002": RuleMeta(
        "RV002",
        "裸 except 捕获",
        "裸 except 会捕获包括 KeyboardInterrupt/SystemExit 在内的所有异常，"
        "容易掩盖真正的错误。",
        "ast.ExceptHandler 节点的 type 字段为 None。",
    ),
    "RV003": RuleMeta(
        "RV003",
        "未使用的导入",
        "未使用的导入增加阅读负担，也会让依赖关系变得不清晰。",
        "ast.Import / ast.ImportFrom 引入的名字在全文件的 ast.Name 引用中"
        "一次都没有出现 (字符串与注释中的文本不参与计数)。",
    ),
    "RV004": RuleMeta(
        "RV004",
        "与单例做相等比较",
        "None/True/False 是单例，应使用 is/is not；用 == 比较会触发对象的"
        "__eq__，结果可能被重载。",
        "ast.Compare 中操作符为 Eq/NotEq，且比较两侧任一是值为 "
        "None/True/False 的 ast.Constant。",
    ),
    "RV005": RuleMeta(
        "RV005",
        "异常被静默吞掉",
        "捕获异常后只有 pass/... 会把失败静默隐藏，使排障困难。",
        "ast.ExceptHandler.body 仅由 ast.Pass (或仅由 Ellipsis 表达式) "
        "组成。",
    ),
    "RV006": RuleMeta(
        "RV006",
        "不可达代码",
        "return/raise/continue/break 之后的语句永远不会执行，通常是遗留或"
        "逻辑错误。",
        "同一语句列表 (body/orelse/finalbody) 中，终止语句之后仍存在的"
        "ast.stmt (定位到第一条不可达语句)。",
    ),
    "RV007": RuleMeta(
        "RV007",
        "同作用域重复函数定义",
        "同一作用域内后定义的同名函数会覆盖先定义的实现，先定义的函数永远"
        "无法按名字调用。",
        "同一父作用域语句列表中出现两个以上同名 FunctionDef/"
        "AsyncFunctionDef (定位到被覆盖的较早定义)。",
    ),
    "RV008": RuleMeta(
        "RV008",
        "调用 eval/exec",
        "eval/exec 执行任意动态代码，存在注入风险，通常有更安全的替代方案。",
        "ast.Call 的 func 是 id 为 eval/exec 的 ast.Name。",
    ),
    "RV009": RuleMeta(
        "RV009",
        "跨文件访问不存在的模块属性",
        "import 项目内模块后访问它没有在顶层定义的名字，运行期会抛 "
        "AttributeError；该规则需要跨文件的语法树索引。",
        "先建立所有文件的顶层定义索引 (函数/类/赋值/导入名)，再检查 "
        "ast.Attribute(value=Name(模块别名)) 或 ast.ImportFrom 的目标名"
        "是否存在于索引中。",
    ),
}


@dataclass
class FileContext:
    """单个被检查文件的上下文。"""

    path: str
    module_name: str
    index: Optional["ProjectIndex"] = None  # noqa: F821 (前向引用)
    structures: Dict[int, str] = field(default_factory=dict)

    def structure_of(self, node: ast.AST) -> str:
        return self.structures.get(id(node), "module")


def _finding(
    rule_id: str,
    message: str,
    node: ast.AST,
    ctx: FileContext,
) -> Finding:
    line = getattr(node, "lineno", 1)
    col = getattr(node, "col_offset", 0)
    return Finding(
        rule_id=rule_id,
        message=message,
        path=ctx.path,
        line=line,
        col=col,
        structure=ctx.structure_of(node),
    )


def build_structure_map(tree: ast.AST) -> Dict[int, str]:
    """为每个节点记录其内层最近的函数/类结构。

    返回 id(node) -> 结构描述。模块级节点映射为 "module"。
    """

    mapping: Dict[int, str] = {}

    def walk(node: ast.AST, structure: str, kind: str, qual: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if kind == "class":
                    child_kind = "method"
                    child_qual = f"{qual}.{child.name}"
                elif kind in ("function", "method", "nested"):
                    child_kind = "nested"
                    child_qual = f"{qual}.<locals>.{child.name}"
                else:
                    child_kind = "function"
                    child_qual = child.name
                child_structure = f"{child_kind}:{child_qual}"
                mapping[id(child)] = child_structure
                walk(child, child_structure, child_kind, child_qual)
            elif isinstance(child, ast.ClassDef):
                child_qual = f"{qual}.{child.name}" if qual else child.name
                child_structure = f"class:{child_qual}"
                mapping[id(child)] = child_structure
                walk(child, child_structure, "class", child_qual)
            else:
                mapping[id(child)] = structure
                walk(child, structure, kind, qual)

    mapping[id(tree)] = "module"
    walk(tree, "module", "module", "")
    return mapping


def _check_mutable_defaults(tree: ast.AST, ctx: FileContext) -> List[Finding]:
    out: List[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        defaults = list(node.args.defaults) + [
            d for d in node.args.kw_defaults if d is not None
        ]
        for default in defaults:
            bad = isinstance(default, (ast.List, ast.Dict, ast.Set))
            if (
                not bad
                and isinstance(default, ast.Call)
                and isinstance(default.func, ast.Name)
                and default.func.id in MUTABLE_FACTORY_CALLS
            ):
                bad = True
            if bad:
                out.append(
                    _finding(
                        "RV001",
                        f"函数 {node.name!r} 使用可变默认参数",
                        default,
                        ctx,
                    )
                )
    return out


def _check_bare_except(tree: ast.AST, ctx: FileContext) -> List[Finding]:
    out: List[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            out.append(_finding("RV002", "裸 except 捕获所有异常", node, ctx))
    return out


def _check_unused_imports(tree: ast.AST, ctx: FileContext) -> List[Finding]:
    imported: List[tuple] = []  # (bound_name, node)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                imported.append((bound, node))
        elif isinstance(node, ast.ImportFrom):
            if node.module == "__future__":
                continue  # __future__ 导入是解释器指令，不参与使用计数
            for alias in node.names:
                if alias.name == "*":
                    continue
                # 项目内模块中不存在的名字由 RV009 报告，此处不再重复
                if (
                    ctx.index is not None
                    and node.module
                    and not node.level
                    and node.module.split(".")[0] in ctx.index.modules
                    and alias.name
                    not in ctx.index.modules[node.module.split(".")[0]]
                ):
                    continue
                bound = alias.asname or alias.name
                imported.append((bound, node))

    used: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
    # __all__ 中显式声明的名字视为被使用 (包重导出)
    for stmt in getattr(tree, "body", []):
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and stmt.targets[0].id == "__all__"
            and isinstance(stmt.value, (ast.List, ast.Tuple))
        ):
            for elt in stmt.value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    used.add(elt.value)

    out: List[Finding] = []
    seen: Set[tuple] = set()
    for bound, node in imported:
        if bound in used:
            continue
        key = (bound, node.lineno)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            _finding(
                "RV003",
                f"导入的名字 {bound!r} 未在代码中使用",
                node,
                ctx,
            )
        )
    return out


def _check_sentinel_equality(tree: ast.AST, ctx: FileContext) -> List[Finding]:
    out: List[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or not node.ops:
            continue
        if not isinstance(node.ops[0], (ast.Eq, ast.NotEq)):
            continue
        sides = [node.left] + node.comparators
        for side in sides:
            if _is_sentinel_constant(side):
                kw = "is/is not"
                out.append(
                    _finding(
                        "RV004",
                        f"与单例 {side.value!r} 做相等比较，应使用 {kw}",
                        node,
                        ctx,
                    )
                )
                break
    return out


def _check_swallowed_except(tree: ast.AST, ctx: FileContext) -> List[Finding]:
    out: List[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.type is None:
            continue  # 裸 except 已由 RV002 报告，避免同一行重复报警
        body = node.body
        if len(body) == 1 and isinstance(body[0], ast.Pass):
            out.append(_finding("RV005", "异常被 pass 静默吞掉", node, ctx))
        elif (
            len(body) == 1
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and body[0].value.value is Ellipsis
        ):
            out.append(_finding("RV005", "异常被 ... 静默吞掉", node, ctx))
    return out


def _check_unreachable(tree: ast.AST, ctx: FileContext) -> List[Finding]:
    out: List[Finding] = []
    stmt_list_fields = ("body", "orelse", "finalbody")
    for node in ast.walk(tree):
        for field_name in stmt_list_fields:
            stmts = getattr(node, field_name, None)
            if not isinstance(stmts, list) or not stmts:
                continue
            if not all(isinstance(s, ast.stmt) for s in stmts):
                continue
            for prev, cur in zip(stmts, stmts[1:]):
                if isinstance(prev, TERMINATORS):
                    out.append(
                        _finding(
                            "RV006",
                            "该语句位于 return/raise/continue/break 之后，"
                            "不可达",
                            cur,
                            ctx,
                        )
                    )
                    break
    return out


def _check_shadowed_function(tree: ast.AST, ctx: FileContext) -> List[Finding]:
    out: List[Finding] = []
    for node in ast.walk(tree):
        stmts = getattr(node, "body", None)
        if isinstance(stmts, list) and stmts:
            first_by_name: Dict[str, ast.FunctionDef] = {}
            for stmt in stmts:
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if stmt.name in first_by_name:
                        first = first_by_name[stmt.name]
                        out.append(
                            _finding(
                                "RV007",
                                f"函数 {stmt.name!r} 在同作用域被重复定义，"
                                "较早的定义被覆盖",
                                first,
                                ctx,
                            )
                        )
                    else:
                        first_by_name[stmt.name] = stmt
    return out


def _check_dynamic_exec(tree: ast.AST, ctx: FileContext) -> List[Finding]:
    out: List[Finding] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in DANGEROUS_BUILTINS
        ):
            out.append(
                _finding(
                    "RV008",
                    f"调用内建 {node.func.id}() 执行动态代码",
                    node,
                    ctx,
                )
            )
    return out


def _check_cross_file(tree: ast.AST, ctx: FileContext) -> List[Finding]:
    """RV009: 需要跨文件索引。"""

    out: List[Finding] = []
    index = ctx.index
    if index is None:
        return out

    # 1) import mod [as alias];  alias.attr / mod.attr
    aliases: Dict[str, str] = {}  # 本文件内绑定名 -> 项目模块名
    for node in ast.walk(tree):
        if not isinstance(node, ast.Import):
            continue
        for alias in node.names:
            top = alias.name.split(".")[0]
            if top in index.modules:
                aliases[alias.asname or top] = top

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in aliases
        ):
            module_name = aliases[node.value.id]
            if node.attr not in index.modules[module_name]:
                out.append(
                    _finding(
                        "RV009",
                        f"模块 {module_name!r} 未定义顶层名字 "
                        f"{node.attr!r}",
                        node,
                        ctx,
                    )
                )

    # 2) from mod import name —— mod 是项目模块且 name 不在其顶层
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level:
            continue
        if node.module is None:
            continue
        top = node.module.split(".")[0]
        if top not in index.modules:
            continue
        defined = index.modules[top]
        for alias in node.names:
            if alias.name not in defined:
                out.append(
                    _finding(
                        "RV009",
                        f"从模块 {top!r} 导入未定义的名字 {alias.name!r}",
                        node,
                        ctx,
                    )
                )
    return out


RuleFunc = Callable[[ast.AST, FileContext], List[Finding]]

INTRA_FILE_RULES: List[RuleFunc] = [
    _check_mutable_defaults,
    _check_bare_except,
    _check_unused_imports,
    _check_sentinel_equality,
    _check_swallowed_except,
    _check_unreachable,
    _check_shadowed_function,
    _check_dynamic_exec,
]
CROSS_FILE_RULES: List[RuleFunc] = [_check_cross_file]
ALL_RULES: List[RuleFunc] = INTRA_FILE_RULES + CROSS_FILE_RULES
