"""Filter language error hierarchy.

Every error is classified with a stable ``code`` and carries the 1-based
``line`` / ``column`` of the offending source location (0 is used when the
location is unknown, e.g. a row-time field error has no source position).
"""


class FilterError(Exception):
    """Base class for every filter-language error."""

    category = "filter-error"

    def __init__(self, message, line=0, column=0, source=""):
        self.message = message
        self.line = line
        self.column = column
        self.source = source
        if line:
            loc = "%d:%d" % (line, column)
            super().__init__("[%s] %s (at %s)" % (self.code, message, loc))
        else:
            super().__init__("[%s] %s" % (self.code, message))

    def describe(self):
        """Return a human readable report with an underlined source snippet."""
        head = "%s: %s" % (self.code, self.message)
        if not self.line or not self.source:
            return head
        lines = self.source.split("\n")
        if self.line - 1 >= len(lines):
            return head
        line_text = lines[self.line - 1]
        caret = " " * max(0, self.column - 1) + "^"
        return "%s\n    | %s\n    | %s" % (head, line_text, caret)


class SyntaxFilterError(FilterError):
    category = "syntax"
    code = "E_SYNTAX"


class MalformedLiteral(FilterError):
    """Malformed number / string literal."""

    category = "syntax"
    code = "E_LITERAL"


class ExpressionTooDeep(FilterError):
    """Parenthesis / expression nesting exceeds the configured limit."""

    category = "resource-limit"
    code = "E_DEPTH"


class ExpressionTooLong(FilterError):
    """Raw source exceeds the configured size limit."""

    category = "resource-limit"
    code = "E_LENGTH"


class ForbiddenConstruct(FilterError):
    """Function call, attribute access, subscript, or other banned syntax."""

    category = "security"
    code = "E_FORBIDDEN"


class UnknownField(FilterError):
    """An identifier in the expression is not a column of the evaluated row."""

    category = "unknown-field"
    code = "E_FIELD"

    def __init__(self, name, line=0, column=0, source=""):
        self.name = name
        super().__init__("unknown field %r" % name, line, column, source)


class TypeMismatch(FilterError):
    """Operands have incompatible runtime types for the operation."""

    category = "type-mismatch"
    code = "E_TYPE"

    def __init__(self, op, left, right, line=0, column=0, source="",
                 unary=False):
        self.op = op
        self.left_type = type_name(left)
        self.right_type = type_name(right)
        self.unary = unary
        if unary:
            msg = "operator %r cannot be applied to %s" % (
                op, self.left_type)
        else:
            msg = "operator %r cannot compare %s with %s" % (
                op, self.left_type, self.right_type)
        super().__init__(msg, line, column, source)


def type_name(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return type(value).__name__
