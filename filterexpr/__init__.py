"""filterexpr —— 安全的过滤条件小型语言（纯标准库，零 eval/exec）。"""

from .api import Expression, compile
from .errors import (
    AttributeAccessNotAllowed,
    ExpressionTooLong,
    FieldMissing,
    FilterError,
    FilterSyntaxError,
    FunctionCallNotAllowed,
    NestingTooDeep,
    SubscriptNotAllowed,
    TypeMismatch,
)
from .parser import DEFAULT_MAX_DEPTH, DEFAULT_MAX_LENGTH

__all__ = [
    "Expression",
    "compile",
    "FilterError",
    "FilterSyntaxError",
    "ExpressionTooLong",
    "NestingTooDeep",
    "FunctionCallNotAllowed",
    "AttributeAccessNotAllowed",
    "SubscriptNotAllowed",
    "FieldMissing",
    "TypeMismatch",
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_MAX_LENGTH",
]
