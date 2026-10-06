# -*- coding: utf-8 -*-
"""envexpand —— 环境变量引用展开与转义（仅标准库）。

语法
----
- ``$NAME``       裸引用：NAME 以字母或下划线开头，后接字母/数字/下划线（贪婪匹配）。
- ``${NAME}``     花括号引用：边界明确，可与其他字符直接相邻。
- ``${${A}}``     嵌套引用：内层先展开，展开结果作为外层的变量名再查表。
- ``$$``          转义：产生一个字面量 ``$``；转义产物不再被解释。
- 单独一个 ``$``（后面不是名字字符、``{`` 或 ``$``）：按字面量原样输出。

展开规则（与依据）
------------------
1. 单次、从左到右扫描模板；识别出的引用被展开后，**展开结果作为字面量
   直接拼接进输出，绝不重新扫描**。依据：POSIX shell 的参数展开只进行
   一次，"the value is not re-scanned for further expansion"；若把替换
   结果放回文本再扫一遍（朴素正则替换法），值里的 ``$`` 会被二次解释，
   既可能注入非预期引用，也可能让终止性无法保证。
2. 每个变量的**值本身视为模板**（make 风格的递归变量）：展开 ``$A`` 时，
   先递归展开 ``env["A"]`` 的值再代入。依据：make 的 recursive 变量语义。
3. 递归展开用「活动集合」做环检测：变量进栈时标记、出栈时清除；再次遇到
   活动中的变量即构成循环引用，立即报错并给出环上的变量名，绝不无限展开。
4. 未定义与空值严格区分：``env = {"E": ""}`` 中 ``$E`` 展开为空串（合法）；
   未定义变量按 ``on_undefined`` 策略处理（默认抛错）。
"""

from __future__ import annotations

__all__ = [
    "ExpansionError",
    "UndefinedVariableError",
    "CircularReferenceError",
    "UnmatchedBraceError",
    "EmptyNameError",
    "EnvExpander",
    "expand_vars",
]


class ExpansionError(Exception):
    """展开相关错误的基类。"""


class UndefinedVariableError(ExpansionError):
    """引用了环境中不存在的变量。"""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__("未定义的变量引用: %r" % name)


class CircularReferenceError(ExpansionError):
    """检测到循环引用。``cycle`` 为环上的变量名（按出现顺序，首尾相接）。"""

    def __init__(self, cycle: list[str], path: list[str]) -> None:
        self.cycle = list(cycle)
        self.path = list(path)
        super().__init__(
            "检测到循环引用: %s（展开路径: %s）"
            % (" -> ".join(self.cycle + [self.cycle[0]]), " -> ".join(self.path))
        )


class UnmatchedBraceError(ExpansionError):
    """``${`` 没有匹配的 ``}``。"""

    def __init__(self, pos: int) -> None:
        self.pos = pos
        super().__init__("第 %d 个字符处的 '${' 没有匹配的 '}'" % pos)


class EmptyNameError(ExpansionError):
    """``${}`` 或展开后得到空的变量名。"""

    def __init__(self) -> None:
        super().__init__("变量名为空（'${}' 或嵌套展开结果为空）")


def _is_name_start(ch: str) -> bool:
    return ch == "_" or ch.isalpha()


def _is_name_char(ch: str) -> bool:
    return ch == "_" or ch.isalnum()


class EnvExpander:
    """环境变量展开器。

    :param env: 变量名 -> 原始值（值本身也是模板，会被递归展开）。
    :param on_undefined: 未定义引用的处理策略：
        ``"raise"``（默认）抛 :class:`UndefinedVariableError`；
        ``"empty"`` 展开为空串；
        ``"keep"``  保留原文（如 ``$NOPE`` 原样输出）；
        可调用对象 ``fn(name) -> str`` 自定义默认值。
    :param trace: 可选回调 ``fn(depth, event, *args)``，用于逐步观察展开过程。
    """

    def __init__(self, env, on_undefined: str = "raise", trace=None) -> None:
        if on_undefined not in ("raise", "empty", "keep") and not callable(on_undefined):
            raise ValueError("on_undefined 必须是 'raise'/'empty'/'keep' 或可调用对象")
        self.env = dict(env)
        self.on_undefined = on_undefined
        self.trace = trace

    # ------------------------------------------------------------------ API

    def expand(self, template: str) -> str:
        """展开模板，返回结果字符串。"""
        memo: dict[str, str] = {}   # 变量名 -> 已展开的值（本次调用内有效）
        active: list[str] = []      # 正在展开中的变量栈（环检测用）
        return self._expand(template, memo, active, 0)

    # ------------------------------------------------------------- internal

    def _log(self, depth: int, event: str, *args) -> None:
        if self.trace is not None:
            self.trace(depth, event, *args)

    def _expand(self, text: str, memo: dict, active: list, depth: int) -> str:
        """单次从左到右扫描；替换结果直接作为字面量输出，绝不重扫。"""
        out: list[str] = []
        i, n = 0, len(text)
        while i < n:
            ch = text[i]
            if ch != "$":
                out.append(ch)
                i += 1
                continue
            # --- ch == '$' ---
            if i + 1 >= n:                      # 结尾的孤立 '$'
                out.append("$")
                i += 1
                continue
            nxt = text[i + 1]
            if nxt == "$":                      # 转义：'$$' -> 字面量 '$'
                out.append("$")
                i += 2
                continue
            if nxt == "{":                      # '${...}'，可嵌套
                end = self._match_brace(text, i)
                name = self._eval_name(text[i + 2 : end], memo, active, depth)
                out.append(self._resolve(name, memo, active, depth))
                i = end + 1
                continue
            if _is_name_start(nxt):             # '$NAME'，贪婪匹配
                j = i + 1
                while j < n and _is_name_char(text[j]):
                    j += 1
                out.append(self._resolve(text[i + 1 : j], memo, active, depth))
                i = j
                continue
            out.append("$")                     # 孤立 '$'，按字面量输出
            i += 1
        return "".join(out)

    def _match_brace(self, text: str, start: int) -> int:
        """返回与 text[start:start+2] == '${' 匹配的 '}' 的下标。"""
        depth = 1
        i = start + 2
        n = len(text)
        while i < n:
            ch = text[i]
            if ch == "$" and i + 1 < n and text[i + 1] == "$":
                i += 2                          # 转义的 '$$' 不参与括号配对
                continue
            if ch == "$" and i + 1 < n and text[i + 1] == "{":
                depth += 1
                i += 2
                continue
            if ch == "}":
                depth -= 1
                if depth == 0:
                    return i
                i += 1
                continue
            i += 1
        raise UnmatchedBraceError(start)

    def _eval_name(self, name_expr: str, memo: dict, active: list, depth: int) -> str:
        """展开 ${...} 内部的名字表达式（内层先展开，结果作为变量名）。"""
        if "$" in name_expr:
            name = self._expand(name_expr, memo, active, depth + 1)
        else:
            name = name_expr
        if not name:
            raise EmptyNameError()
        return name

    def _resolve(self, name: str, memo: dict, active: list, depth: int) -> str:
        """查表并递归展开变量的值；负责环检测与未定义策略。"""
        self._log(depth, "ref", name)
        if name in memo:
            self._log(depth, "cached", name, memo[name])
            return memo[name]
        if name in active:                      # 环：立即报告，绝不无限展开
            cycle = active[active.index(name):]
            self._log(depth, "cycle", cycle)
            raise CircularReferenceError(cycle, active + [name])
        if name not in self.env:
            self._log(depth, "undefined", name)
            return self._handle_undefined(name)
        raw = self.env[name]
        if raw == "":                           # 空值：合法，展开为空串
            self._log(depth, "empty", name)
            memo[name] = ""
            return ""
        active.append(name)
        try:
            value = self._expand(raw, memo, active, depth + 1)
        finally:
            active.pop()
        memo[name] = value
        self._log(depth, "value", name, value)
        return value

    def _handle_undefined(self, name: str) -> str:
        policy = self.on_undefined
        if policy == "raise":
            raise UndefinedVariableError(name)
        if policy == "empty":
            return ""
        if policy == "keep":
            return "${" + name + "}"
        return str(policy(name))


def expand_vars(template: str, env, on_undefined: str = "raise") -> str:
    """便捷函数：``EnvExpander(env, on_undefined).expand(template)``。"""
    return EnvExpander(env, on_undefined=on_undefined).expand(template)
