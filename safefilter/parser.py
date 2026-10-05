"""Hand-written recursive-descent parser.

Grammar (EBNF) and precedence, lowest to highest:

    expr        := or_expr
    or_expr     := and_expr ( "or" and_expr )*          # left associative
    and_expr    := not_expr ( "and" not_expr )*         # left associative
    not_expr    := "not" not_expr | comparison          # right associative
    comparison  := primary ( cmp_op primary )?          # non-associative
    cmp_op      := "==" | "!=" | "<" | "<=" | ">" | ">="
    primary     := NUMBER | STRING | "true" | "false" | "null"
                 | IDENT | "(" expr ")"

Chained comparisons such as ``a < b < c`` are a syntax error.
Function calls ``f(...)``, attribute access ``a.b`` and subscripts ``a[i]``
are recognised by the parser and rejected as forbidden constructs.

AST nodes are plain tuples (fast to build and to walk):
    ("lit", value)
    ("ident", name, line, col)
    ("cmp", op, left, right, line, col)
    ("not", operand)
    ("and", left, right) / ("or", left, right)
"""

from .errors import (ExpressionTooDeep, ExpressionTooLong,
                     ForbiddenConstruct, SyntaxFilterError)
from .lexer import tokenize

DEFAULT_MAX_DEPTH = 64
DEFAULT_MAX_LENGTH = 10000

_CMP_OPS = ("==", "!=", "<", "<=", ">", ">=")


class _Parser:
    def __init__(self, source, max_depth):
        self.source = source
        self.tokens = tokenize(source)
        self.pos = 0
        self.depth = 0
        self.max_depth = max_depth

    def peek(self):
        return self.tokens[self.pos]

    def advance(self):
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def error(self, message, tok=None):
        tok = tok or self.peek()
        raise SyntaxFilterError(message, tok[2], tok[3], self.source)

    def parse(self):
        node = self.parse_or()
        tok = self.peek()
        if tok[0] != "EOF":
            if tok[0] == "(":
                raise ForbiddenConstruct(
                    "function calls are not allowed",
                    tok[2], tok[3], self.source)
            if tok[0] == ".":
                raise ForbiddenConstruct(
                    "attribute access is not allowed",
                    tok[2], tok[3], self.source)
            if tok[0] == "[":
                raise ForbiddenConstruct(
                    "subscript access is not allowed",
                    tok[2], tok[3], self.source)
            self.error("unexpected trailing input %r" % tok[1], tok)
        return node

    def parse_or(self):
        left = self.parse_and()
        while self.peek()[0] == "OR":
            self.advance()
            right = self.parse_and()
            left = ("or", left, right)
        return left

    def parse_and(self):
        left = self.parse_not()
        while self.peek()[0] == "AND":
            self.advance()
            right = self.parse_not()
            left = ("and", left, right)
        return left

    def parse_not(self):
        tok = self.peek()
        if tok[0] == "NOT":
            self.advance()
            self.depth += 1
            if self.depth > self.max_depth:
                raise ExpressionTooDeep(
                    "expression nesting exceeds limit of %d" % self.max_depth,
                    tok[2], tok[3], self.source)
            operand = self.parse_not()
            self.depth -= 1
            return ("not", operand)
        return self.parse_comparison()

    def parse_comparison(self):
        left = self.parse_primary()
        tok = self.peek()
        if tok[0] in _CMP_OPS:
            self.advance()
            right = self.parse_primary()
            nxt = self.peek()
            if nxt[0] in _CMP_OPS:
                self.error(
                    "chained comparisons are not allowed; "
                    "use 'and' to combine them", nxt)
            return ("cmp", tok[0], left, right, tok[2], tok[3])
        return left

    def parse_primary(self):
        tok = self.advance()
        kind = tok[0]
        if kind == "NUMBER" or kind == "STRING":
            node = ("lit", tok[1])
        elif kind == "TRUE":
            node = ("lit", True)
        elif kind == "FALSE":
            node = ("lit", False)
        elif kind == "NULL":
            node = ("lit", None)
        elif kind == "IDENT":
            node = ("ident", tok[1], tok[2], tok[3])
        elif kind == "(":
            self.depth += 1
            if self.depth > self.max_depth:
                raise ExpressionTooDeep(
                    "expression nesting exceeds limit of %d" % self.max_depth,
                    tok[2], tok[3], self.source)
            node = self.parse_or()
            closing = self.peek()
            if closing[0] != ")":
                self.error("expected ')'", closing)
            self.advance()
            self.depth -= 1
        elif kind == ".":
            raise ForbiddenConstruct(
                "attribute access is not allowed",
                tok[2], tok[3], self.source)
        elif kind == "[":
            raise ForbiddenConstruct(
                "subscript access is not allowed",
                tok[2], tok[3], self.source)
        elif kind == ")":
            self.error("unexpected ')'", tok)
        elif kind == "EOF":
            self.error("unexpected end of expression", tok)
        else:
            self.error("unexpected %r" % tok[1], tok)

        nxt = self.peek()
        if nxt[0] == "(":
            raise ForbiddenConstruct(
                "function calls are not allowed",
                nxt[2], nxt[3], self.source)
        if nxt[0] == ".":
            raise ForbiddenConstruct(
                "attribute access is not allowed",
                nxt[2], nxt[3], self.source)
        if nxt[0] == "[":
            raise ForbiddenConstruct(
                "subscript access is not allowed",
                nxt[2], nxt[3], self.source)
        return node


def parse(source, max_depth=DEFAULT_MAX_DEPTH, max_length=DEFAULT_MAX_LENGTH):
    """Parse ``source`` into an AST tuple tree. Raises FilterError subclasses."""
    if not isinstance(source, str):
        raise TypeError("expression source must be a string")
    if len(source) > max_length:
        raise ExpressionTooLong(
            "expression length %d exceeds limit of %d"
            % (len(source), max_length), 1, 1, source[:200])
    return _Parser(source, max_depth).parse()
