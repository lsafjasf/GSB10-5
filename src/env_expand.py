"""环境变量展开库（仅标准库）。

语法
----
- ``$NAME`` / ``${NAME}``：变量引用。``NAME`` 由字母、数字、下划线组成，
  且首字符不能是数字（与 POSIX shell 的 name 规则一致）。
- ``${...}`` 的大括号内允许再出现任意模板，例如 ``${A_${B}}``：
  内层先展开，其结果作为外层的变量名；该结果**不会**被再次扫描成模板。
- 转义：字面文本中 ``\\$`` 表示一个字面 ``$``，``\\\\`` 表示一个字面 ``\\``。
  转义只在解析阶段生效，产出的是 Literal 记号，后续任何替换都不会再解释它。

展开语义
--------
模板只解析一次，得到 ``Literal`` 与 ``Ref`` 组成的 AST；递归仅发生在
"变量的值"上（值需要先展开再使用）。因此每个字符都不会被解释第二遍——
先替换进去的内容（尤其是含 ``$`` 的值）不会被重复扫描。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Union


class ExpandError(Exception):
    """展开过程中所有错误的基类。"""


class SyntaxExpandError(ExpandError):
    """模板语法错误（如未闭合的 ``${``、空变量名）。"""


class UndefinedVariableError(ExpandError):
    """引用了未定义变量。"""

    def __init__(self, name: str, referrer: Optional[str]):
        self.name = name
        self.referrer = referrer
        where = f"（在变量 {referrer!r} 的值中）" if referrer else ""
        super().__init__(f"未定义变量: {name!r}{where}")


class CycleError(ExpandError):
    """检测到循环引用。

    ``path`` 为解析栈，形如 ``["A", "B", "C", "A"]``；
    ``cycle`` 为环本身，形如 ``["A", "B", "C"]``（A -> B -> C -> A）。
    """

    def __init__(self, path: List[str]):
        self.path = path
        repeat = path[-1]
        start = path.index(repeat)
        self.cycle = path[start:-1]
        chain = " -> ".join(self.cycle + [repeat])
        super().__init__(f"检测到循环引用: {chain}")


@dataclass(frozen=True)
class Literal:
    text: str


@dataclass(frozen=True)
class Ref:
    # 变量名本身也可能是模板（${A_${B}}），故为节点列表；$NAME 形式下为单元素
    inner: List["Node"]


Node = Union[Literal, Ref]


def _is_name_start(ch: str) -> bool:
    return ch.isalpha() or ch == "_"


def _is_name_cont(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def parse(template: str, in_braces: bool = False):
    """把模板解析为 ``(节点列表, 结束符位置)``。

    ``in_braces=True`` 表示当前正在解析 ``${...}`` 的内部，此时 ``}``
    是结束符；否则 ``}`` 是普通字面字符。结束符位置仅在命中时非 ``None``。
    """

    nodes: List[Node] = []
    buf: List[str] = []
    i = 0
    n = len(template)

    def flush():
        if buf:
            nodes.append(Literal("".join(buf)))
            buf.clear()

    while i < n:
        ch = template[i]
        if ch == "\\":
            nxt = template[i + 1] if i + 1 < n else ""
            if nxt in ("$", "\\"):
                buf.append(nxt)  # 转义记号：逐字保留，永不重新解释
                i += 2
            else:
                buf.append(ch)  # 反斜杠仅对 $ 与自身特殊，其余原样保留
                i += 1
        elif ch == "$":
            if i + 1 < n and template[i + 1] == "{":
                flush()
                inner, end = parse(template[i + 2 :], in_braces=True)
                if end is None:
                    raise SyntaxExpandError("未闭合的 ${：缺少 }")
                if not inner:
                    raise SyntaxExpandError("空变量引用: ${}")
                nodes.append(Ref(inner))
                i = i + 2 + end + 1
            elif i + 1 < n and _is_name_start(template[i + 1]):
                flush()
                j = i + 1
                while j < n and _is_name_cont(template[j]):
                    j += 1
                nodes.append(Ref([Literal(template[i + 1 : j])]))
                i = j
            else:
                buf.append(ch)  # $ 后不能构成引用，按字面 $ 处理
                i += 1
        elif ch == "}" and in_braces:
            flush()
            return nodes, i
        elif ch == "}":
            buf.append(ch)
            i += 1
        else:
            buf.append(ch)
            i += 1

    flush()
    return nodes, None


# 未定义变量的处理策略：
#   error -> 抛 UndefinedVariableError（默认）
#   empty -> 展开为空串（与"定义为空值"结果相同，但错误信息中可区分）
#   keep  -> 原样保留引用文本（常用于兼容类 shell 场景）
UndefinedPolicy = str


def expand(
    template: str,
    env: Optional[Dict[str, str]] = None,
    *,
    undefined: UndefinedPolicy = "error",
    _trace: Optional[List[tuple]] = None,
) -> str:
    """展开模板。

    ``env`` 中不存在的键视为未定义；存在但值为 ``""`` 视为已定义空值，
    正常展开为空串。
    """

    if undefined not in ("error", "empty", "keep"):
        raise ValueError("undefined 必须是 error / empty / keep 之一")
    env = dict(env or {})
    ast, _ = parse(template)
    return _render(ast, env, undefined, stack=[], cache={}, trace=_trace)


def _render(
    nodes: List[Node],
    env: Dict[str, str],
    undefined: UndefinedPolicy,
    stack: List[str],
    cache: Dict[str, str],
    trace: Optional[List[tuple]],
) -> str:
    parts: List[str] = []
    for node in nodes:
        if isinstance(node, Literal):
            parts.append(node.text)
            continue
        name = _render(node.inner, env, undefined, stack, cache, trace)
        if name == "":
            raise SyntaxExpandError("变量名为空（大括号内未解析出名字）")
        if name in stack:
            raise CycleError(stack + [name])
        if name in cache:
            value = cache[name]
            if trace is not None:
                trace.append(("use-cached", name, value))
        elif name in env:
            stack.append(name)
            if trace is not None:
                trace.append(("enter", name, env[name]))
            value_ast, _ = parse(env[name])
            value = _render(value_ast, env, undefined, stack, cache, trace)
            stack.pop()
            cache[name] = value
            if trace is not None:
                trace.append(("leave", name, value))
        elif undefined == "empty":
            value = ""
            if trace is not None:
                trace.append(("undefined-empty", name, ""))
        elif undefined == "keep":
            value = "${" + name + "}"
            if trace is not None:
                trace.append(("undefined-keep", name, value))
        else:
            raise UndefinedVariableError(name, stack[-1] if stack else None)
        parts.append(value)
    return "".join(parts)
