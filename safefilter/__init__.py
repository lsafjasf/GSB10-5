"""safefilter: a safe filter-condition parser and evaluator.

Only the Python standard library is used, and the host language's own
evaluation facilities (eval/exec/compile/getattr) are never touched.
"""

from .errors import (ExpressionTooDeep, ExpressionTooLong, FilterError,
                     ForbiddenConstruct, MalformedLiteral, SyntaxFilterError,
                     TypeMismatch, UnknownField)
from .evaluator import CompiledExpression, compile_expr, evaluate
from .parser import parse

__all__ = [
    "compile_expr", "evaluate", "parse", "CompiledExpression",
    "FilterError", "SyntaxFilterError", "MalformedLiteral",
    "ExpressionTooDeep", "ExpressionTooLong", "ForbiddenConstruct",
    "UnknownField", "TypeMismatch",
]

__version__ = "1.0.0"
