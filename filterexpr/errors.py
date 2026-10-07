"""过滤表达式统一错误类型。

所有错误都携带位置信息（offset 从 0 开始，line/col 从 1 开始），
并通过 category 分类，便于调用方区分：

- syntax         语法错误（非法字符、括号不匹配、残留 token 等）
- security       恶意构造（函数调用、属性访问、下标访问、超长、超深嵌套）
- field_missing  求值时行内不存在该字段
- type_mismatch  求值时运算符两侧类型不匹配
"""

from __future__ import annotations


class FilterError(Exception):
    """所有过滤表达式错误的基类。"""

    category = "error"

    def __init__(self, message, offset=0, line=1, col=1, source=None):
        super().__init__(message)
        self.message = message
        self.offset = offset
        self.line = line
        self.col = col
        self.source = source

    def __str__(self):
        return "[%s] 行%d 列%d: %s" % (self.category, self.line, self.col, self.message)

    def pretty(self):
        """带源码行与指示符的可读输出。"""
        head = str(self)
        if not self.source:
            return head
        lines = self.source.splitlines()
        if 1 <= self.line <= len(lines):
            text = lines[self.line - 1]
            return "%s\n  %s\n  %s^" % (head, text, " " * max(self.col - 1, 0))
        return head


class FilterSyntaxError(FilterError):
    category = "syntax"


class ExpressionTooLong(FilterError):
    category = "security"


class NestingTooDeep(FilterError):
    category = "security"


class FunctionCallNotAllowed(FilterError):
    category = "security"


class AttributeAccessNotAllowed(FilterError):
    category = "security"


class SubscriptNotAllowed(FilterError):
    category = "security"


class EvaluationError(FilterError):
    """求值期错误基类（不携带源码行）。"""

    def __init__(self, message):
        FilterError.__init__(self, message, 0, 1, 1, None)


class FieldMissing(EvaluationError):
    category = "field_missing"


class TypeMismatch(EvaluationError):
    category = "type_mismatch"
