"""Parser/evaluator for CLDR plural rule strings (LDML plural rule syntax).

This is an independent implementation used to cross-check the hand-written
rules in plural.py: it evaluates the exact rule text published by CLDR
(stored in cldr_data.py), instead of reusing the hand-written functions.

Supported syntax (all of cardinal/ordinal rules for en, ru, ar, fr, ja):
  expr        := operand | operand '%' integer
  relation    := expr ('=' | '!=') rangeList
  rangeList   := value (',' value)*        with value := number | number '..' number
  condition   := andExpr ('or' andExpr)*
  andExpr     := relation ('and' relation)*
"""
from __future__ import annotations

import re
from fractions import Fraction


def _num(text):
    return Fraction(text.strip())


def _parse_relation(text):
    if "!=" in text:
        left, right = text.split("!=", 1)
        expect_equal = False
    elif "=" in text:
        left, right = text.split("=", 1)
        expect_equal = True
    else:
        raise ValueError(f"bad relation: {text!r}")
    left = left.strip()
    if "%" in left:
        name, mod = left.split("%", 1)
        operand_name, modulus = name.strip(), int(mod)
    else:
        operand_name, modulus = left, None
    ranges = []
    for item in right.split(","):
        item = item.strip()
        if ".." in item:
            lo, hi = item.split("..", 1)
            ranges.append((_num(lo), _num(hi)))
        else:
            v = _num(item)
            ranges.append((v, v))

    def relation(op):
        value = getattr(op, operand_name)
        if modulus is not None:
            value = value % modulus
        matched = any(lo <= value <= hi for lo, hi in ranges)
        return matched if expect_equal else not matched

    return relation


def _parse_and(text):
    relations = [_parse_relation(part) for part in text.split(" and ")]
    return lambda op: all(rel(op) for rel in relations)


def parse_rule(rule_text):
    """Return predicate(Operands) -> bool for a CLDR rule string."""
    branches = [_parse_and(part)
                for part in rule_text.split(" or ")]
    return lambda op: any(branch(op) for branch in branches)


def evaluate(rules, op):
    """Evaluate a {count: rule_text} table in CLDR order; 'other' fallback."""
    for count in ("zero", "one", "two", "few", "many", "other"):
        rule = rules.get(count)
        if rule and parse_rule(rule)(op):
            return count
    return "other"


# --- sample expansion of the @integer / @decimal sample lists -------------

def _format_fraction(value, digits):
    scale = 10 ** digits
    numerator = (value * scale).numerator
    assert (value * scale).denominator == 1
    if digits == 0:
        return str(numerator)
    whole, rem = divmod(abs(numerator), scale)
    sign = "-" if numerator < 0 else ""
    return f"{sign}{whole}.{rem:0{digits}d}"


def _range_values(a, b):
    fa, fb = Fraction(a), Fraction(b)
    decimals = max(
        len(a.split(".", 1)[1]) if "." in a else 0,
        len(b.split(".", 1)[1]) if "." in b else 0)
    if decimals == 0 and abs(fb - fa) <= 20:
        return [str(int(fa) + k) for k in range(int(fb - fa) + 1)]
    values = [a, b]
    mid = (fa + fb) / 2
    mid_digits = decimals
    scale = 10 ** mid_digits
    while (mid * scale).denominator != 1 and mid_digits < 12:
        mid_digits += 1
        scale = 10 ** mid_digits
    values.insert(1, _format_fraction(mid, mid_digits))
    if decimals == 0:
        values.insert(1, a if int(fa) + 1 > int(fb) else str(int(fa) + 1))
    return values


def expand_samples(sample_text):
    """Expand a CLDR sample string to concrete numeric strings.

    Compact-notation samples (containing 'c', e.g. 1c6) are skipped: the
    library does not implement scientific/compact notation.
    """
    sample_text = re.sub(r"@?\b(integer|decimal)\b", ",", sample_text)
    values = []
    for token in sample_text.split(","):
        token = token.strip()
        if not token or token in ("…", "...") or "c" in token:
            continue
        if "~" in token:
            a, b = token.split("~", 1)
            values.extend(_range_values(a.strip(), b.strip()))
        else:
            values.append(token)
    return values
