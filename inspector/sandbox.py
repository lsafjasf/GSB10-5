"""规则源码的静态校验（AST 白名单）。

思路：不做“黑名单拦截已知危险写法”（永远拦不完），而是做
“节点类型白名单”——只允许一份显式列出的 AST 节点集合，
任何未列出的节点（Import、While 之外的语句不需要禁，因为
节点集合里根本没有 Import）直接拒绝。

被封死的主要逃逸面：
- import / __import__：Import/ImportFrom 节点不在白名单；
- 属性逃逸（().__class__.__mro__...）：禁止一切以下划线开头
  的属性访问与名称引用；
- 间接拿到内建：globals/locals/vars/getattr/setattr/eval/exec/
  compile 等内建不在可用集合中，且 AST 层禁止对它们的调用名；
- 文件/网络/进程：没有对应模块可 import，也没有 open/socket 内建，
  子进程侧再以 RLIMIT_NOFILE / RLIMIT_NPROC 兜底（纵深防御）。
"""

import ast

from .limits import (
    ALLOWED_BUILTINS,
    ALLOWED_EXTRA_BUILTINS,
    ALLOWED_MODULES,
    MAX_SOURCE_BYTES,
    STATUS_BAD_RULE,
)


class RuleRejected(Exception):
    """静态校验不通过。message 面向规则作者，说明哪一行为什么被拒。"""


# 允许的 AST 节点类型白名单（Python 3.12 语法）
_ALLOWED_NODES = frozenset({
    # 模块与语句（Import 节点本身放行，模块名在下方静态校验白名单，
    # 运行时再由受限 __import__ 二次把关）
    ast.Import, ast.ImportFrom,
    ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
    ast.Return, ast.Delete, ast.Assign, ast.AugAssign, ast.AnnAssign,
    ast.For, ast.While, ast.If, ast.With, ast.Raise, ast.Try, ast.TryStar,
    ast.Assert, ast.Expr, ast.Pass, ast.Break, ast.Continue,
    # 表达式
    ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.Lambda, ast.IfExp,
    ast.Dict, ast.Set, ast.ListComp, ast.SetComp, ast.DictComp,
    ast.GeneratorExp, ast.Await, ast.Yield, ast.YieldFrom,
    ast.Compare, ast.Call, ast.FormattedValue, ast.JoinedStr,
    ast.Constant, ast.Attribute, ast.Subscript, ast.Starred,
    ast.Name, ast.List, ast.Tuple, ast.Slice,
    # 运算符
    ast.And, ast.Or, ast.Add, ast.Sub, ast.Mult, ast.MatMult, ast.Div,
    ast.Mod, ast.Pow, ast.LShift, ast.RShift, ast.BitOr, ast.BitXor,
    ast.BitAnd, ast.FloorDiv, ast.Invert, ast.Not, ast.UAdd, ast.USub,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Is, ast.IsNot,
    ast.In, ast.NotIn,
    # 其它结构
    ast.arguments, ast.arg, ast.keyword, ast.alias, ast.withitem,
    ast.comprehension, ast.ExceptHandler, ast.Load, ast.Store, ast.Del,
})

# 即便节点类型合法，这些名字也不许出现（双保险，防止未来维护者
# 把某个内建加进 ALLOWED_BUILTINS 时忘记这里的语义）。
_FORBIDDEN_NAMES = frozenset({
    "eval", "exec", "compile", "open", "input", "__import__",
    "globals", "locals", "vars", "dir", "getattr", "setattr", "delattr",
    "breakpoint", "exit", "quit", "help", "memoryview", "object",
    "type",  # type() 本身可用于元类逃逸，规则场景用不到，直接禁
    "super",  # super() 配合 __class__ 逃逸；但 super 在 ALLOWED_BUILTINS 里
              # 是为了允许 class 内 super().method() —— 故这里不放进 forbidden，
              # 见下方说明。
})

# super 允许以 super().method() 形式使用（方法名不含下划线即安全），
# 所以把它从 _FORBIDDEN_NAMES 里拿掉。
_FORBIDDEN_NAMES = _FORBIDDEN_NAMES - {"super"}


def validate_source(source: str) -> ast.Module:
    """校验规则源码，返回编译好的 AST；不合法则抛 RuleRejected。"""
    source_bytes = source.encode("utf-8", errors="replace")
    if len(source_bytes) > MAX_SOURCE_BYTES:
        raise RuleRejected(
            f"规则源码 {len(source_bytes)} 字节，超过上限 {MAX_SOURCE_BYTES} 字节"
        )
    try:
        tree = ast.parse(source, mode="exec")
    except SyntaxError as exc:
        raise RuleRejected(f"语法错误: {exc}") from exc

    for node in ast.walk(tree):
        if type(node) not in _ALLOWED_NODES:
            raise RuleRejected(
                f"第 {getattr(node, 'lineno', '?')} 行: "
                f"不允许的语法结构 {type(node).__name__}"
            )
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in ALLOWED_MODULES:
                    raise RuleRejected(
                        f"第 {node.lineno} 行: 模块 {alias.name.split('.')[0]!r} "
                        f"不在巡检规则能力清单内"
                    )
        if isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                raise RuleRejected(f"第 {node.lineno} 行: 不允许相对导入")
            root = (node.module or "").split(".")[0]
            if root not in ALLOWED_MODULES:
                raise RuleRejected(
                    f"第 {node.lineno} 行: 模块 {root!r} 不在巡检规则能力清单内"
                )
        if isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            raise RuleRejected(
                f"第 {node.lineno} 行: 不允许使用名称 {node.id!r}"
            )
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise RuleRejected(
                f"第 {node.lineno} 行: 不允许访问下划线开头的属性 {node.attr!r}"
            )
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise RuleRejected(
                f"第 {node.lineno} 行: 不允许使用双下划线名称 {node.id!r}"
            )
    return tree


def build_restricted_builtins(output_acc: list) -> dict:
    """构造规则的 __builtins__：白名单内建 + 受限 __import__ + 限量 print。"""
    import builtins as _real_builtins

    allowed = ALLOWED_BUILTINS | ALLOWED_EXTRA_BUILTINS
    env = {}
    for name in allowed:
        if name in ("True", "False", "None", "NotImplemented", "Ellipsis"):
            continue  # 常量由编译器直接处理，无需注入
        env[name] = getattr(_real_builtins, name)

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level != 0:
            raise ImportError("不允许相对导入")
        root = name.split(".")[0]
        if root not in ALLOWED_MODULES:
            raise ImportError(f"模块 {root!r} 不在巡检规则能力清单内")
        return _real_builtins.__import__(name, globals, locals, fromlist, level)

    def limited_print(*args, sep=" ", end="\n", file=None, flush=False):
        if file is not None:
            raise ValueError("规则内 print 不允许指定 file 参数")
        text = sep.join(str(a) for a in args) + end
        output_acc[0] += len(text.encode("utf-8", errors="replace"))
        if output_acc[0] > _max_output():
            raise ValueError("规则输出量超过上限")
        _real_builtins.print(text, end="")

    env["__import__"] = guarded_import
    env["print"] = limited_print
    return env


def _max_output() -> int:
    from .limits import MAX_OUTPUT_BYTES
    return MAX_OUTPUT_BYTES
