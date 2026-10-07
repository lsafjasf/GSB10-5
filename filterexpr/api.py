"""对外接口：编译一次，多次求值。"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import FieldMissing, FilterError, TypeMismatch
from .evaluator import evaluate
from .parser import DEFAULT_MAX_DEPTH, DEFAULT_MAX_LENGTH, parse


class Expression:
    """编译后的过滤表达式。AST 在构造时生成一次，之后对行反复求值。"""

    def __init__(
        self,
        source,
        max_depth=DEFAULT_MAX_DEPTH,
        max_length=DEFAULT_MAX_LENGTH,
    ):
        self.source = source
        self.max_depth = max_depth
        self.max_length = max_length
        self.ast = parse(source, max_depth, max_length)

    def evaluate(self, row):
        """对一行求值，返回 True/False；错误以异常形式上报。"""
        return evaluate(self.ast, row)

    __call__ = evaluate

    def evaluate_many(self, rows):
        """
        对可迭代的多行求值。

        返回 (results, errors)：
        - results 为每行一个布尔值；
        - 出错的行在 results 中放 None，并在 errors 中记录
          (行号, category, message)，绝不静默当假。
        """
        results = []
        errors = []
        for index, row in enumerate(rows):
            try:
                results.append(evaluate(self.ast, row))
            except (FieldMissing, TypeMismatch) as exc:
                results.append(None)
                errors.append((index, exc.category, exc.message))
        return results, errors


def compile(source, max_depth=DEFAULT_MAX_DEPTH, max_length=DEFAULT_MAX_LENGTH):
    """编译入口（与内置函数同名但为本模块的局部名字）。"""
    return Expression(source, max_depth, max_length)
