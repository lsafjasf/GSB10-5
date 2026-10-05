"""Random, type-aware filter-expression generator used for differential
testing against a Python-semantics oracle.

The generated expressions are guaranteed well-typed for the generated row,
so the filter language and Python must agree on the boolean result.
"""

import random

SCHEMA = {
    "age": "number",
    "score": "number",
    "level": "number",
    "vip": "bool",
    "active": "bool",
    "city": "string",
    "name": "string",
    "nick": "string",
    "grade": "number",
}

_FIELDS_BY_TYPE = {
    "number": [f for f, t in SCHEMA.items() if t == "number"],
    "bool": [f for f, t in SCHEMA.items() if t == "bool"],
    "string": [f for f, t in SCHEMA.items() if t == "string"],
}

_NUM_LITERALS = [0, 1, 18, 30, 60, 100, 0.5, 2.5, 99.9]
_STR_LITERALS = ["beijing", "shanghai", "tokyo", "alpha", ""]
_BOOL_LITERALS = ["true", "false"]


def random_row(rng):
    return {
        "age": rng.randint(0, 99),
        "score": round(rng.uniform(0.0, 1.0), 3),
        "level": rng.randint(1, 5),
        "vip": rng.choice([True, False]),
        "active": rng.choice([True, False]),
        "city": rng.choice(["beijing", "shanghai", "tokyo"]),
        "name": rng.choice(["alice", "bob", "carol"]),
        "nick": rng.choice(["alpha", "beta", "gamma"]),
        "grade": rng.randint(0, 100),
    }


def _typed_atom(rng, typ):
    """Return (filter_text, python_text) for a value atom of ``typ``."""
    use_field = rng.random() < 0.5
    if use_field:
        fname = rng.choice(_FIELDS_BY_TYPE[typ])
        return fname, fname
    if typ == "number":
        v = rng.choice(_NUM_LITERALS)
        return repr(v), repr(v)
    if typ == "bool":
        t = rng.choice(_BOOL_LITERALS)
        return t, t.title()
    v = rng.choice(_STR_LITERALS)
    return "'%s'" % v, repr(v)


def _comparison(rng):
    typ = rng.choice(["number", "number", "string", "bool"])
    left_f, left_p = _typed_atom(rng, typ)
    right_f, right_p = _typed_atom(rng, typ)
    if typ == "bool":
        op = rng.choice(["==", "!="])
    else:
        op = rng.choice(["==", "!=", "<", "<=", ">", ">="])
    return "%s %s %s" % (left_f, op, right_f), \
           "%s %s %s" % (left_p, op, right_p)


def _bool_expr(rng, depth):
    if depth <= 0 or rng.random() < 0.45:
        return _comparison(rng)
    kind = rng.choice(["and", "or", "not"])
    if kind == "not":
        f, p = _bool_expr(rng, depth - 1)
        return "not " + f, "not " + p
    f1, p1 = _bool_expr(rng, depth - 1)
    f2, p2 = _bool_expr(rng, depth - 1)
    return "%s %s %s" % (f1, kind, f2), "%s %s %s" % (p1, kind, p2)


def generate_case(rng, depth=3):
    """Return (filter_source, row, python_oracle_source)."""
    row = random_row(rng)
    filter_text, py_text = _bool_expr(rng, depth)
    return filter_text, row, py_text
