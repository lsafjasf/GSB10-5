"""Plural category selection linked with number formatting.

Pure standard library. Rules follow the Unicode CLDR (release 48)
Language Plural Rules:
  https://www.unicode.org/cldr/charts/48/supplemental/language_plural_rules.html

The category is always computed from the *formatted* string, exactly as
ICU/CLDR prescribe: the visible fraction digits (v/w/f/t operands) decide
the category, so changing `decimals` can change the result.

Operands (LDML):
  n  absolute value of the formatted number
  i  integer digits of n
  v  number of visible fraction digits, with trailing zeros
  w  number of visible fraction digits, without trailing zeros
  f  visible fraction digits, with trailing zeros, as an integer
  t  visible fraction digits, without trailing zeros, as an integer
  e  exponent (always 0 here: no scientific/compact notation)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction

__all__ = ["Operands", "format_number", "operands_of", "plural_category",
           "SUPPORTED_LOCALES", "ORDINAL_SUFFIX_EN"]

_NUMBER_RE = re.compile(r"^[+-]?(\d+)(?:\.(\d+))?$")


@dataclass(frozen=True)
class Operands:
    n: Fraction  # absolute value
    i: int       # integer digits
    v: int       # visible fraction digits, with trailing zeros
    w: int       # visible fraction digits, without trailing zeros
    f: int       # fraction digits with trailing zeros, as integer
    t: int       # fraction digits without trailing zeros, as integer
    e: int = 0   # exponent (compact/scientific notation not used)


def _to_decimal_text(value) -> str:
    """Normalize int/float/str/Decimal/Fraction to a plain decimal string."""
    if isinstance(value, bool):
        raise TypeError("bool is not a number here")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Fraction):
        if value.denominator == 1:
            return str(value.numerator)
        return format(Decimal(value.numerator) / Decimal(value.denominator), "f")
    if isinstance(value, float):
        return format(Decimal(str(value)), "f")
    if isinstance(value, str):
        text = value.strip().replace(",", "").replace("_", "")
        if not _NUMBER_RE.match(text):
            raise ValueError(f"not a plain decimal number: {value!r}")
        return text
    raise TypeError(f"unsupported type: {type(value).__name__}")


def format_number(value, decimals=None, thousands_sep=",", decimal_point="."):
    """Format a number; returns the display string.

    decimals=None keeps the input's own fraction digits; otherwise the
    value is rounded (half-up) to exactly `decimals` fraction digits.
    """
    text = _to_decimal_text(value)
    neg = text.startswith("-")
    if neg:
        text = text[1:]
    int_part, _, frac_part = text.partition(".")
    if decimals is not None:
        if decimals < 0:
            raise ValueError("decimals must be >= 0")
        quantum = Decimal(1).scaleb(-decimals)
        rounded = Decimal(int_part + "." + (frac_part or "0")).quantize(
            quantum, rounding=ROUND_HALF_UP)
        rounded_text = format(rounded, "f")
        int_part, _, frac_part = rounded_text.partition(".")
    if thousands_sep:
        groups = []
        while len(int_part) > 3:
            groups.append(int_part[-3:])
            int_part = int_part[:-3]
        groups.append(int_part)
        int_part = thousands_sep.join(reversed(groups))
    out = int_part
    if frac_part:
        out += decimal_point + frac_part
    return ("-" if neg else "") + out


def operands_of(formatted) -> Operands:
    """Compute CLDR operands from a formatted number (string or number)."""
    text = _to_decimal_text(formatted)
    if text.startswith(("+", "-")):
        text = text[1:]
    int_part, _, frac_part = text.partition(".")
    int_part = int_part or "0"
    stripped = frac_part.rstrip("0")
    return Operands(
        n=Fraction(int(int_part) * (10 ** len(frac_part)) + int(frac_part or 0),
                   10 ** len(frac_part)),
        i=int(int_part),
        v=len(frac_part),
        w=len(stripped),
        f=int(frac_part) if frac_part else 0,
        t=int(stripped) if stripped else 0,
    )


# --- hand-written rule sets, transcribed from CLDR 48 (see cldr_data.py) ---

def _in(value, lo, hi):
    return lo <= value <= hi


def _en_cardinal(op):
    return "one" if op.i == 1 and op.v == 0 else "other"


def _en_ordinal(op):
    if op.n % 10 == 1 and op.n % 100 != 11:
        return "one"
    if op.n % 10 == 2 and op.n % 100 != 12:
        return "two"
    if op.n % 10 == 3 and op.n % 100 != 13:
        return "few"
    return "other"


def _ru_cardinal(op):
    if op.v == 0 and op.i % 10 == 1 and op.i % 100 != 11:
        return "one"
    if op.v == 0 and _in(op.i % 10, 2, 4) and not _in(op.i % 100, 12, 14):
        return "few"
    if op.v == 0 and (op.i % 10 == 0 or _in(op.i % 10, 5, 9)
                      or _in(op.i % 100, 11, 14)):
        return "many"
    return "other"


def _ar_cardinal(op):
    n = op.n
    if n == 0:
        return "zero"
    if n == 1:
        return "one"
    if n == 2:
        return "two"
    if _in(n % 100, 3, 10):
        return "few"
    if _in(n % 100, 11, 99):
        return "many"
    return "other"


def _fr_cardinal(op):
    if op.i in (0, 1):
        return "one"
    if op.e == 0 and op.i != 0 and op.i % 1_000_000 == 0 and op.v == 0:
        return "many"
    return "other"


def _fr_ordinal(op):
    return "one" if op.n == 1 else "other"


def _only_other(op):
    return "other"


_CARDINAL_RULES = {
    "en": _en_cardinal,
    "ru": _ru_cardinal,
    "ar": _ar_cardinal,
    "fr": _fr_cardinal,
    "ja": _only_other,
}

_ORDINAL_RULES = {
    "en": _en_ordinal,
    "fr": _fr_ordinal,
    "ru": _only_other,
    "ar": _only_other,
    "ja": _only_other,
}

SUPPORTED_LOCALES = tuple(_CARDINAL_RULES)

ORDINAL_SUFFIX_EN = {"one": "st", "two": "nd", "few": "rd", "other": "th"}


def plural_category(locale, value, decimals=None, ordinal=False,
                    thousands_sep=","):
    """Select the CLDR plural category for `value` in `locale`.

    The number is formatted first (decimals / thousands separator), and the
    category is derived from the formatted result, so formatting options can
    change the category. Returns one of: zero, one, two, few, many, other.
    """
    table = _ORDINAL_RULES if ordinal else _CARDINAL_RULES
    try:
        rule = table[locale]
    except KeyError:
        raise ValueError(f"unsupported locale: {locale!r}") from None
    formatted = format_number(value, decimals=decimals,
                              thousands_sep=thousands_sep)
    return rule(operands_of(formatted))
