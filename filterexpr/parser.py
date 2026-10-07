"""手写递归下降语法分析器。

文法（优先级从低到高，详见 GRAMMAR.md）：

    or_expr  := and_expr ( "or"  and_expr )*
    and_expr := not_expr ( "and" not_expr )*
    not_expr := "not" not_expr | cmp_expr
    cmp_expr := unary ( CMP_OP unary )?          # 不允许链式比较
    unary    := "-" unary | primary
    primary  := NUMBER | STRING | "true" | "false" | "null"
              | IDENT ["(" ...]    # 紧跟 ( 一律按函数调用拒绝
              | "(" or_expr ")"

安全闸门：
- 源码长度超过 max_length 直接拒绝（ExpressionTooLong）。
- 括号嵌套、not/一元负号链深度超过 max_depth 拒绝（NestingTooDeep）。
- 任何函数调用、属性访问（.）、下标访问（[ ]）在语法层拒绝。
"""

from __future__ import annotations

from . import nodes as N
from .errors import (
    AttributeAccessNotAllowed,
    ExpressionTooLong,
    FilterSyntaxError,
    FunctionCallNotAllowed,
    NestingTooDeep,
    SubscriptNotAllowed,
)
from .lexer import tokenize

DEFAULT_MAX_DEPTH = 50
DEFAULT_MAX_LENGTH = 10000


class Parser:
    def __init__(self, source, max_depth=DEFAULT_MAX_DEPTH, max_length=DEFAULT_MAX_LENGTH):
        self.source = source
        self.max_depth = max_depth
        self.max_length = max_length
        self.depth = 0
        if len(source) > max_length:
            raise ExpressionTooLong(
                "表达式长度 %d 超过上限 %d 个字符" % (len(source), max_length),
                0,
                1,
                1,
                source,
            )
        self.tokens = tokenize(source)
        self.pos = 0

    # ---- 基础工具 ----

    def peek(self):
        return self.tokens[self.pos]

    def advance(self):
        tok = self.tokens[self.pos]
        if tok.type != "EOF":
            self.pos += 1
        return tok

    def error(self, msg, tok):
        raise FilterSyntaxError(msg, tok.offset, tok.line, tok.col, self.source)

    def enter(self, tok):
        """进入一层嵌套结构（括号 / not / 一元负号）时的深度检查。"""
        self.depth += 1
        if self.depth > self.max_depth:
            raise NestingTooDeep(
                "嵌套深度超过上限 %d 层" % self.max_depth,
                tok.offset,
                tok.line,
                tok.col,
                self.source,
            )

    def leave(self):
        self.depth -= 1

    # ---- 递归下降 ----

    def parse(self):
        tok = self.peek()
        if tok.type == "EOF":
            self.error("空表达式", tok)
        root = self.parse_or()
        tok = self.peek()
        if tok.type != "EOF":
            self.error("表达式结束后存在多余内容 %r" % tok.value, tok)
        return root

    def parse_or(self):
        left = self.parse_and()
        while self.peek().type == "OR":
            tok = self.advance()
            right = self.parse_and()
            left = N.at(N.node(N.OR, left, right), tok.offset, tok.line, tok.col)
        return left

    def parse_and(self):
        left = self.parse_not()
        while self.peek().type == "AND":
            tok = self.advance()
            right = self.parse_not()
            left = N.at(N.node(N.AND, left, right), tok.offset, tok.line, tok.col)
        return left

    def parse_not(self):
        tok = self.peek()
        if tok.type == "NOT":
            self.advance()
            self.enter(tok)
            operand = self.parse_not()
            self.leave()
            return N.at(N.node(N.NOT, operand), tok.offset, tok.line, tok.col)
        return self.parse_cmp()

    def parse_cmp(self):
        left = self.parse_unary()
        tok = self.peek()
        if tok.type != "OP" or tok.value not in N.CMP_OPS:
            return left
        self.advance()
        right = self.parse_unary()
        node = N.at(N.node(N.CMP, tok.value, left, right), tok.offset, tok.line, tok.col)
        nxt = self.peek()
        if nxt.type == "OP" and nxt.value in N.CMP_OPS:
            self.error("不支持链式比较（如 a < b < c），请用 and/or 显式连接", nxt)
        return node

    def parse_unary(self):
        tok = self.peek()
        if tok.type == "MINUS":
            self.advance()
            self.enter(tok)
            operand = self.parse_unary()
            self.leave()
            return N.at(N.node(N.NEG, operand), tok.offset, tok.line, tok.col)
        return self.parse_primary()

    def parse_primary(self):
        tok = self.peek()
        ttype = tok.type

        if ttype == "NUMBER" or ttype == "STRING":
            self.advance()
            node = N.at(N.node(N.LIT, tok.value), tok.offset, tok.line, tok.col)
        elif ttype == "TRUE":
            self.advance()
            node = N.at(N.node(N.LIT, True), tok.offset, tok.line, tok.col)
        elif ttype == "FALSE":
            self.advance()
            node = N.at(N.node(N.LIT, False), tok.offset, tok.line, tok.col)
        elif ttype == "NULL":
            self.advance()
            node = N.at(N.node(N.LIT, None), tok.offset, tok.line, tok.col)
        elif ttype == "IDENT":
            self.advance()
            node = N.at(N.node(N.FIELD, tok.value), tok.offset, tok.line, tok.col)
            if self.peek().type == "LPAREN":
                lp = self.peek()
                raise FunctionCallNotAllowed(
                    "禁止函数调用：%r 后的 '('" % tok.value,
                    lp.offset,
                    lp.line,
                    lp.col,
                    self.source,
                )
        elif ttype == "LPAREN":
            self.advance()
            self.enter(tok)
            node = self.parse_or()
            self.leave()
            close = self.peek()
            if close.type != "RPAREN":
                self.error("缺少右括号 ')'", close)
            self.advance()
        else:
            if ttype == "EOF":
                self.error("表达式意外结束，此处缺少操作数", tok)
            self.error("此处缺少操作数（字段、字面量或括号表达式），意外的 %r" % tok.value, tok)

        self.check_postfix()
        return node

    def check_postfix(self):
        tok = self.peek()
        if tok.type == "DOT":
            raise AttributeAccessNotAllowed(
                "禁止属性访问：'.' 不被允许",
                tok.offset,
                tok.line,
                tok.col,
                self.source,
            )
        if tok.type == "LBRACKET":
            raise SubscriptNotAllowed(
                "禁止下标访问：'[' 不被允许",
                tok.offset,
                tok.line,
                tok.col,
                self.source,
            )
        if tok.type == "RBRACKET":
            raise SubscriptNotAllowed(
                "禁止下标访问：多余的 ']'",
                tok.offset,
                tok.line,
                tok.col,
                self.source,
            )
        if tok.type == "LPAREN":
            raise FunctionCallNotAllowed(
                "禁止函数调用：表达式后不允许出现 '('",
                tok.offset,
                tok.line,
                tok.col,
                self.source,
            )


def parse(source, max_depth=DEFAULT_MAX_DEPTH, max_length=DEFAULT_MAX_LENGTH):
    """解析源码，返回 AST 根节点。"""
    return Parser(source, max_depth, max_length).parse()
