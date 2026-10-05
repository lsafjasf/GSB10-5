"""CLDR plural-category selection coupled with number formatting.

Standard library only. Rules implement the CLDR Language Plural Rules
(chart v47), see data/cldr_rules_samples.json for the extracted rule text
and sample values used for differential testing.

Key design point: the plural category is computed from the CLDR "operands"
of the *formatted* number string, never from the raw numeric value alone.
Changing fraction digits or switching to ordinal changes the operands and
therefore can change the category.
"""
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from typing import NamedTuple, Union

Number = Union[int, float, str, Decimal]

CATEGORIES = ("zero", "one", "two", "few", "many", "other")


class Operands(NamedTuple):
    """CLDR plural operands (https://unicode.org/reports/tr35/tr35-numbers.html#Operands)."""
    n: Decimal  # absolute value of the source number
    i: int      # integer digits of n
    v: int      # number of visible fraction digits, with trailing zeros
    w: int      # number of visible fraction digits, without trailing zeros
    f: int      # visible fraction digits, with trailing zeros, as integer
    t: int      # visible fraction digits, without trailing zeros, as integer
    e: int      # exponent of compact/scientific notation ("1.2c6" -> e=6)


def operands(value: Number, e: int = 0) -> Operands:
    """Compute CLDR operands from a number or its decimal string form.

    String input keeps its exact fraction digits: operands("1.0").v == 1
    while operands("1").v == 0 -- this is what couples plural selection to
    number formatting. The CLDR compact sample notation "1.0000001c6"
    (mantissa 'c' exponent) is also accepted.
    """
    if isinstance(value, str):
        text = value.strip().replace(",", "").replace(" ", "")
        if "c" in text:  # CLDR compact sample notation, e.g. "1.0000001c6"
            text, exp = text.split("c", 1)
            e = int(exp)
        dec = Decimal(text)
        if e:  # TR35: with a compact exponent, n/i/v/w/f/t are computed
            with localcontext() as ctx:  # after shifting the decimal point
                ctx.prec = max(50, len(dec.as_tuple().digits) + e + 10)
                dec = dec.scaleb(e)
    elif isinstance(value, Decimal):
        dec = value
    elif isinstance(value, float):
        dec = Decimal(repr(value))
    elif isinstance(value, int):
        dec = Decimal(value)
    else:
        raise TypeError("unsupported number type: %r" % type(value))
    dec = dec.copy_abs()  # context-free, unlike abs() which rounds at prec=28
    plain = format(dec, "f")
    int_part, _, frac_part = plain.partition(".")
    stripped = frac_part.rstrip("0")
    return Operands(
        n=dec,
        i=int(int_part),
        v=len(frac_part),
        w=len(stripped),
        f=int(frac_part) if frac_part else 0,
        t=int(stripped) if stripped else 0,
        e=e,
    )


# --- CLDR rules, chart v47 (see data/cldr_rules_samples.json) --------------

def _en_cardinal(op: Operands) -> str:
    # one: i = 1 and v = 0
    if op.i == 1 and op.v == 0:
        return "one"
    return "other"


def _en_ordinal(op: Operands) -> str:
    if op.n != op.i:  # n % 10 = k can only hold for integers
        return "other"
    n10, n100 = op.i % 10, op.i % 100
    if n10 == 1 and n100 != 11:
        return "one"
    if n10 == 2 and n100 != 12:
        return "two"
    if n10 == 3 and n100 != 13:
        return "few"
    return "other"


def _ru_cardinal(op: Operands) -> str:
    if op.v == 0:
        i10, i100 = op.i % 10, op.i % 100
        if i10 == 1 and i100 != 11:
            return "one"
        if 2 <= i10 <= 4 and not 12 <= i100 <= 14:
            return "few"
        if i10 == 0 or 5 <= i10 <= 9 or 11 <= i100 <= 14:
            return "many"
    return "other"


def _ru_ordinal(op: Operands) -> str:
    return "other"


def _ar_cardinal(op: Operands) -> str:
    n = op.n
    if n == 0:
        return "zero"
    if n == 1:
        return "one"
    if n == 2:
        return "two"
    if op.f != 0:
        return "other"  # n % 100 is non-integral; CLDR ranges hold integers
    n100 = op.i % 100
    if 3 <= n100 <= 10:
        return "few"
    if 11 <= n100 <= 99:
        return "many"
    return "other"


def _ar_ordinal(op: Operands) -> str:
    return "other"


def _fr_cardinal(op: Operands) -> str:
    # one: i = 0,1
    if op.i in (0, 1):
        return "one"
    # many: e = 0 and i != 0 and i % 1000000 = 0 and v = 0, or e != 0..5
    if (op.e == 0 and op.i != 0 and op.i % 1000000 == 0 and op.v == 0) or op.e > 5:
        return "many"
    return "other"


def _fr_ordinal(op: Operands) -> str:
    # one: n = 1
    if op.n == 1:
        return "one"
    return "other"


_RULES = {
    "en": (_en_cardinal, _en_ordinal),
    "ru": (_ru_cardinal, _ru_ordinal),
    "ar": (_ar_cardinal, _ar_ordinal),
    "fr": (_fr_cardinal, _fr_ordinal),
}


def plural_category(lang: str, value: Number, ordinal: bool = False, e: int = 0) -> str:
    """CLDR plural category of `value` in `lang` ('en'|'ru'|'ar'|'fr')."""
    try:
        cardinal, ordinal_fn = _RULES[lang]
    except KeyError:
        raise ValueError("unsupported language: %r" % lang)
    op = operands(value, e=e)
    return (ordinal_fn if ordinal else cardinal)(op)


# --- number formatting ------------------------------------------------------

def format_number(value: Number, min_frac: int = 0, max_frac: int = 3,
                  grouping: bool = True, group_sep: str = ",",
                  decimal_sep: str = ".") -> str:
    """Format a number with fraction-digit limits and thousands grouping.

    Rounding is ROUND_HALF_EVEN (ICU default). The returned string is what
    plural selection consumes, so fraction digits chosen here directly
    affect the category.
    """
    if not 0 <= min_frac <= max_frac:
        raise ValueError("require 0 <= min_frac <= max_frac")
    dec = value if isinstance(value, Decimal) else Decimal(
        value.strip() if isinstance(value, str) else
        repr(value) if isinstance(value, float) else value)
    with localcontext() as ctx:
        ctx.prec = max(50, len(dec.as_tuple().digits) + max_frac + 10)
        dec = dec.quantize(Decimal(1).scaleb(-max_frac), rounding=ROUND_HALF_EVEN)
    plain = format(dec, "f")
    int_part, _, frac_part = plain.partition(".")
    frac_part = frac_part.rstrip("0")
    frac_part = frac_part[:max(min_frac, len(frac_part))].ljust(min_frac, "0")
    sign = ""
    if int_part.startswith("-"):
        sign, int_part = "-", int_part[1:]
    if grouping:
        groups = []
        while len(int_part) > 3:
            groups.append(int_part[-3:])
            int_part = int_part[:-3]
        groups.append(int_part)
        int_part = group_sep.join(reversed(groups))
    return sign + int_part + (decimal_sep + frac_part if frac_part else "")


class PluralResult(NamedTuple):
    category: str
    formatted: str
    operands: Operands


def select_plural(lang: str, value: Number, ordinal: bool = False,
                  min_frac: int = None, max_frac: int = None,
                  grouping: bool = True, e: int = 0) -> PluralResult:
    """Format `value`, then pick the CLDR category of the formatted string.

    When min_frac/max_frac are None the value's own digits are used as-is.
    Otherwise the number is formatted first and the operands are computed
    from the formatted output, so formatting options can change the result
    (e.g. en: 1 -> "one", but 1 rendered as "1.0" -> "other").
    """
    try:
        cardinal, ordinal_fn = _RULES[lang]
    except KeyError:
        raise ValueError("unsupported language: %r" % lang)
    if min_frac is None and max_frac is None:
        op = operands(value, e=e)
        formatted = format_number(value, min_frac=op.v, max_frac=op.v,
                                  grouping=grouping)
    else:
        formatted = format_number(value, min_frac or 0,
                                  max_frac if max_frac is not None else 3,
                                  grouping=grouping)
        op = operands(formatted, e=e)
    category = (ordinal_fn if ordinal else cardinal)(op)
    return PluralResult(category=category, formatted=formatted, operands=op)
