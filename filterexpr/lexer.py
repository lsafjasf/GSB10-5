"""手写词法分析器（scanner），不依赖任何语言自带求值能力。

词法规则：
- 空白（空格/制表/换行等）仅作分隔，换行会推进行号。
- 数字：整数或浮点（123、12.5、.5、1e3、1.5e-2），不允许前导的 +。
- 字符串：单/双引号，支持 \\n \\t \\r \\\\ \\" \\' \\xNN \\uNNNN 等转义。
- 标识符/关键字：[A-Za-z_][A-Za-z0-9_]*；and or not true false null 为关键字。
- 运算符：== != <= >= < > ( ) ；其余符号一律报语法错误并给出位置。
"""

from __future__ import annotations

from .errors import FilterSyntaxError

KEYWORDS = {
    "and": "AND",
    "or": "OR",
    "not": "NOT",
    "true": "TRUE",
    "false": "FALSE",
    "null": "NULL",
}

TWO_CHAR_OPS = {"==", "!=", "<=", ">="}
ONE_CHAR_OPS = {
    "<": "OP",
    ">": "OP",
    "(": "LPAREN",
    ")": "RPAREN",
    "[": "LBRACKET",
    "]": "RBRACKET",
    ".": "DOT",
    "-": "MINUS",
}

_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "0": "\0",
    "\\": "\\",
    '"': '"',
    "'": "'",
    "/": "/",
    "b": "\b",
    "f": "\f",
}


class Token:
    __slots__ = ("type", "value", "offset", "line", "col")

    def __init__(self, type_, value, offset, line, col):
        self.type = type_
        self.value = value
        self.offset = offset
        self.line = line
        self.col = col

    def __repr__(self):
        return "Token(%s, %r, @%d:%d)" % (self.type, self.value, self.line, self.col)


def _is_ident_start(ch):
    return ch.isalpha() or ch == "_"


def _is_ident_part(ch):
    return ch.isalnum() or ch == "_"


def tokenize(source):
    """把源码切成 token 序列，末尾追加 EOF token。"""
    tokens = []
    i = 0
    n = len(source)
    line = 1
    line_start = 0

    def err(msg, offset, ln, col):
        raise FilterSyntaxError(msg, offset, ln, col, source)

    while i < n:
        ch = source[i]
        if ch in " \t\r":
            i += 1
            continue
        if ch == "\n":
            line += 1
            line_start = i + 1
            i += 1
            continue

        col = i - line_start + 1

        if ch.isdigit() or (ch == "." and i + 1 < n and source[i + 1].isdigit()):
            start = i
            if ch == ".":
                i += 1
                while i < n and source[i].isdigit():
                    i += 1
                is_float = True
            else:
                while i < n and source[i].isdigit():
                    i += 1
                is_float = False
                if i < n and source[i] == ".":
                    is_float = True
                    i += 1
                    while i < n and source[i].isdigit():
                        i += 1
            if i < n and source[i] in "eE":
                j = i + 1
                if j < n and source[j] in "+-":
                    j += 1
                if j < n and source[j].isdigit():
                    is_float = True
                    i = j
                    while i < n and source[i].isdigit():
                        i += 1
            text = source[start:i]
            try:
                value = float(text) if is_float else int(text)
            except ValueError:
                err("非法数字字面量 %r" % text, start, line, col)
            tokens.append(Token("NUMBER", value, start, line, col))
            continue

        if ch == '"' or ch == "'":
            quote = ch
            start = i
            i += 1
            buf = []
            while True:
                if i >= n:
                    err("字符串未闭合（缺少 %s）" % quote, start, line, col)
                c = source[i]
                if c == "\n":
                    err("字符串内不允许直接换行", i, line, i - line_start + 1)
                if c == quote:
                    i += 1
                    break
                if c == "\\":
                    i += 1
                    if i >= n:
                        err("字符串未闭合（转义后缺少内容）", start, line, col)
                    e = source[i]
                    if e in _ESCAPES:
                        buf.append(_ESCAPES[e])
                        i += 1
                    elif e == "x":
                        hexs = source[i + 1 : i + 3]
                        if len(hexs) != 2 or any(c2 not in "0123456789abcdefABCDEF" for c2 in hexs):
                            err("非法 \\x 转义", i, line, i - line_start + 1)
                        buf.append(chr(int(hexs, 16)))
                        i += 3
                    elif e == "u":
                        hexs = source[i + 1 : i + 5]
                        if len(hexs) != 4 or any(c2 not in "0123456789abcdefABCDEF" for c2 in hexs):
                            err("非法 \\u 转义", i, line, i - line_start + 1)
                        buf.append(chr(int(hexs, 16)))
                        i += 5
                    else:
                        err("未知转义序列 \\%s" % e, i, line, i - line_start + 1)
                    continue
                buf.append(c)
                i += 1
            tokens.append(Token("STRING", "".join(buf), start, line, col))
            continue

        if _is_ident_start(ch):
            start = i
            while i < n and _is_ident_part(source[i]):
                i += 1
            word = source[start:i]
            ttype = KEYWORDS.get(word)
            if ttype is not None:
                tokens.append(Token(ttype, word, start, line, col))
            else:
                tokens.append(Token("IDENT", word, start, line, col))
            continue

        two = source[i : i + 2]
        if two in TWO_CHAR_OPS:
            tokens.append(Token("OP", two, i, line, col))
            i += 2
            continue
        if ch in ONE_CHAR_OPS:
            tokens.append(Token(ONE_CHAR_OPS[ch], ch, i, line, col))
            i += 1
            continue
        if ch == "=":
            err("发现单个 '='，相等比较请使用 '=='", i, line, col)
        if ch == "!":
            err("发现单个 '!'，逻辑非请使用 not，不等请使用 !=", i, line, col)
        err("非法字符 %r" % ch, i, line, col)

    tokens.append(Token("EOF", None, n, line, n - line_start + 1))
    return tokens
