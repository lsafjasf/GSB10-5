"""Generalization hierarchies for quasi-identifiers.

Each transform maps a raw value to a more general representation. Specs are
strings so they can come from the CLI, e.g. "age_band:10" or "zip_prefix:3".
"""

from __future__ import annotations

from typing import Callable, Dict

Transform = Callable[[str], str]


def identity(value: str) -> str:
    return value


def mask(value: str) -> str:
    return "*"


def age_band(width: int = 5) -> Transform:
    if width <= 0:
        raise ValueError("age band width must be positive")

    def transform(value: str) -> str:
        value = value.strip()
        if not value.lstrip("-").isdigit():
            return value  # already generalized or masked
        age = int(value)
        low = (age // width) * width
        return "%d-%d" % (low, low + width - 1)

    return transform


def zip_prefix(length: int = 3) -> Transform:
    if length < 0:
        raise ValueError("zip prefix length must be >= 0")

    def transform(value: str) -> str:
        value = value.strip()
        if length >= len(value):
            return value
        return value[:length] + "*" * (len(value) - length)

    return transform


def date_to_month(value: str) -> str:
    return value.strip()[:7]


def date_to_year(value: str) -> str:
    return value.strip()[:4]


# Demo occupation taxonomy: detailed occupation -> category.
_OCCUPATION_CATEGORY = {
    "teacher": "education",
    "professor": "education",
    "nurse": "health",
    "doctor": "health",
    "engineer": "tech",
    "programmer": "tech",
    "accountant": "business",
    "sales": "business",
    "manager": "business",
    "clerk": "office",
    "driver": "logistics",
    "farmer": "agriculture",
    "astronaut": "specialist",
    "matador": "specialist",
}


def occupation_category(value: str) -> str:
    return _OCCUPATION_CATEGORY.get(value.strip().lower(), "other")


def get_transform(spec: str) -> Transform:
    """Resolve a spec like 'identity', 'age_band:10', 'zip_prefix:3'."""
    name, _, arg = spec.partition(":")
    name = name.strip()
    if name == "identity":
        return identity
    if name == "mask":
        return mask
    if name == "age_band":
        return age_band(int(arg) if arg else 5)
    if name == "zip_prefix":
        return zip_prefix(int(arg) if arg else 3)
    if name == "date_month":
        return date_to_month
    if name == "date_year":
        return date_to_year
    if name == "occupation_category":
        return occupation_category
    raise ValueError("unknown generalization spec: %r" % spec)


def parse_generalize_arg(text: str) -> Dict[str, str]:
    """Parse 'age=age_band:10,zipcode=zip_prefix:3' into a field->spec dict."""
    specs: Dict[str, str] = {}
    if not text:
        return specs
    for item in text.split(","):
        field, sep, spec = item.partition("=")
        if not sep:
            raise ValueError("bad --generalize item %r (want field=spec)" % item)
        get_transform(spec)  # validate early
        specs[field.strip()] = spec.strip()
    return specs
