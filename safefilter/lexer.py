"""Hand-written lexer for the filter language.

The scanner is a plain character loop; it never delegates to ``eval``,
``compile``, ``getattr`` or any host-language parser.

Token shape: ``(kind, value, line, column)`` with 1-based line/column.
"""

from .errors import MalformedLiteral, SyntaxFilterError

KEYWORDS = {"and", "or", "not", "true", "false", "null"}


def tokenize(source):
    tokens = []
    append = tokens.append
    n = len(source)
    i = 0
    line = 1
    line_start = 0

    def col_at(pos):
        return pos - line_start + 1

    while i < n:
        ch = source[i]

        if ch in " \t\r":
            i += 1
            continue
        if ch == "\n":
            line += 1
            i += 1
            line_start = i
            continue

        start = i
        start_line, start_col = line, col_at(i)

        if ch.isalpha() or ch == "_":
            i += 1
            while i < n and (source[i].isalnum() or source[i] == "_"):
                i += 1
            word = source[start:i]
            kind = word.upper() if word in KEYWORDS else "IDENT"
            append((kind, word, start_line, start_col))
            continue

        if ch.isdigit():
            i += 1
            while i < n and source[i].isdigit():
                i += 1
            if i < n and source[i] == ".":
                if i + 1 >= n or not source[i + 1].isdigit():
                    raise MalformedLiteral(
                        "malformed number: digits required after decimal point",
                        line, col_at(i) + 1, source)
                i += 1
                while i < n and source[i].isdigit():
                    i += 1
            if i < n and source[i] in "eE":
                exp_pos = i
                i += 1
                if i < n and source[i] in "+-":
                    i += 1
                if i >= n or not source[i].isdigit():
                    raise MalformedLiteral(
                        "malformed number: exponent digits required",
                        line, col_at(exp_pos) + 1, source)
                while i < n and source[i].isdigit():
                    i += 1
            text = source[start:i]
            append(("NUMBER", float(text) if "." in text or "e" in text
                    else int(text), start_line, start_col))
            continue

        if ch == "." or ch == "[" or ch == "]":
            append((ch, ch, line, start_col))
            i += 1
            continue

        if ch == "(" or ch == ")":
            append((ch, ch, line, start_col))
            i += 1
            continue

        if ch in "'\"":
            quote = ch
            i += 1
            buf = []
            while i < n and source[i] != quote:
                c = source[i]
                if c == "\n":
                    raise MalformedLiteral(
                        "unterminated string literal",
                        start_line, start_col, source)
                if c == "\\":
                    esc_pos = i
                    i += 1
                    if i >= n:
                        raise MalformedLiteral(
                            "unterminated string literal",
                            start_line, start_col, source)
                    esc = source[i]
                    mapping = {"n": "\n", "t": "\t", "r": "\r",
                               "\\": "\\", "'": "'", '"': '"',
                               "0": "\0", "b": "\b", "f": "\f",
                               "/": "/"}
                    if esc not in mapping:
                        raise MalformedLiteral(
                            "invalid string escape \\%s" % esc,
                            line, col_at(esc_pos) + 1, source)
                    buf.append(mapping[esc])
                    i += 1
                else:
                    buf.append(c)
                    i += 1
            if i >= n:
                raise MalformedLiteral(
                    "unterminated string literal",
                    start_line, start_col, source)
            i += 1
            append(("STRING", "".join(buf), start_line, start_col))
            continue

        two = source[i:i + 2]
        if two in ("==", "!=", ">=", "<="):
            append((two, two, line, start_col))
            i += 2
            continue
        if ch in "><":
            append((ch, ch, line, start_col))
            i += 1
            continue

        raise SyntaxFilterError(
            "unexpected character %r" % ch, line, start_col, source)

    append(("EOF", None, line, col_at(n) if n else 1))
    return tokens
