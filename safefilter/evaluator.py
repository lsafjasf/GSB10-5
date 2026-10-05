"""AST evaluator.

The AST is interpreted by compiling it into a tree of plain Python
closures.  No host-language evaluation facility (``eval``/``exec``/
``compile``/``getattr``) is used anywhere: operators are dispatched by
explicit tag comparison and applied with hard-coded Python operators.

Type rules (strict, never silently false):

* ``and`` / ``or`` / ``not`` require bool operands -> TypeMismatch otherwise.
* ``==`` / ``!=`` require both operands in the same category
  (null / bool / number / string); int and float are both "number".
  ``null == null`` is True, ``null == <anything else>`` is False.
* ``<`` ``<=`` ``>`` ``>=`` require two numbers or two strings.
* Referencing a field that is absent from the row -> UnknownField.
"""

from .errors import TypeMismatch, UnknownField
from .parser import parse

_NUMBER_TYPES = (int, float)


def _category(value):
    if value is None:
        return 0
    t = type(value)
    if t is bool:
        return 1
    if t is int or t is float:
        return 2
    if t is str:
        return 3
    return -1


def _compile(node, make_accessor, source):
    """Compile an AST tuple into ``fn(row)`` using ``make_accessor`` to
    resolve identifiers.  ``make_accessor(name, line, col)`` must return a
    function taking the row and producing the field value."""
    tag = node[0]

    if tag == "lit":
        value = node[1]
        return lambda row: value

    if tag == "ident":
        return make_accessor(node[1], node[2], node[3])

    if tag == "not":
        operand = _compile(node[1], make_accessor, source)

        def do_not(row, _op=operand):
            value = _op(row)
            if type(value) is not bool:
                raise TypeMismatch("not", value, None, unary=True)
            return not value
        return do_not

    if tag == "and":
        left = _compile(node[1], make_accessor, source)
        right = _compile(node[2], make_accessor, source)

        def do_and(row, _l=left, _r=right):
            lv = _l(row)
            if type(lv) is not bool:
                raise TypeMismatch("and", lv, None, unary=True)
            if not lv:
                return False
            rv = _r(row)
            if type(rv) is not bool:
                raise TypeMismatch("and", rv, None, unary=True)
            return rv
        return do_and

    if tag == "or":
        left = _compile(node[1], make_accessor, source)
        right = _compile(node[2], make_accessor, source)

        def do_or(row, _l=left, _r=right):
            lv = _l(row)
            if type(lv) is not bool:
                raise TypeMismatch("or", lv, None, unary=True)
            if lv:
                return True
            rv = _r(row)
            if type(rv) is not bool:
                raise TypeMismatch("or", rv, None, unary=True)
            return rv
        return do_or

    # tag == "cmp"
    op = node[1]
    left = _compile(node[2], make_accessor, source)
    right = _compile(node[3], make_accessor, source)
    line, col = node[4], node[5]

    if op == "==" or op == "!=":
        want = op == "=="

        def do_eq(row, _l=left, _r=right, _op=op, _line=line, _col=col,
                  _want=want, _src=source):
            a = _l(row)
            b = _r(row)
            if a is None or b is None:
                result = a is None and b is None
            else:
                ta = type(a)
                tb = type(b)
                if ta is tb:
                    result = a == b
                elif (ta is int or ta is float) and \
                        (tb is int or tb is float):
                    result = a == b
                else:
                    raise TypeMismatch(_op, a, b, _line, _col, _src)
            return result if _want else not result
        return do_eq

    def do_order(row, _l=left, _r=right, _op=op, _line=line, _col=col,
                 _src=source):
        a = _l(row)
        b = _r(row)
        ta = type(a)
        tb = type(b)
        if (ta is int or ta is float) and (tb is int or tb is float):
            pass
        elif ta is str and tb is str:
            pass
        else:
            raise TypeMismatch(_op, a, b, _line, _col, _src)
        if _op == "<":
            return a < b
        if _op == "<=":
            return a <= b
        if _op == ">":
            return a > b
        return a >= b
    return do_order


def _row_accessor(source):
    def make(name, line, col):
        def get(row, _name=name, _line=line, _col=col, _src=source):
            try:
                return row[_name]
            except KeyError:
                raise UnknownField(_name, _line, _col, _src) from None
        return get
    return make


def _column_accessor(columns, source):
    def make(name, line, col):
        try:
            column = columns[name]
        except KeyError:
            raise UnknownField(name, line, col, source) from None
        return lambda i, _c=column: _c[i]
    return make


class CompiledExpression:
    """A parsed, ready-to-evaluate filter expression."""

    def __init__(self, source, ast):
        self.source = source
        self.ast = ast

    def evaluate(self, row):
        """Evaluate against a mapping (dict-like) row. Returns bool."""
        fn = _compile(self.ast, _row_accessor(self.source), self.source)
        result = fn(row)
        if type(result) is not bool:
            raise TypeMismatch("<top-level>", result, None, unary=True)
        return result

    def bind_columns(self, columns):
        """Bind to columnar storage ``{name: sequence}`` for bulk scans."""
        fn = _compile(self.ast, _column_accessor(columns, self.source),
                      self.source)

        def count_between(start, stop, _fn=fn):
            """Count matching rows with index in [start, stop)."""
            matches = 0
            for i in range(start, stop):
                if _fn(i):
                    matches += 1
            return matches

        return count_between


def compile_expr(source, max_depth=64, max_length=10000):
    """Parse and compile ``source`` into a CompiledExpression."""
    return CompiledExpression(source, parse(source, max_depth, max_length))


def evaluate(source, row):
    """One-shot helper: parse ``source`` and evaluate it against ``row``."""
    return compile_expr(source).evaluate(row)
