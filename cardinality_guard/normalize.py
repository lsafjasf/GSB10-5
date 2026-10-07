"""Value normalisation: collapse dynamic value shapes into stable templates.

The safety property of template normalisation is that it is shape based:
two values are merged only when they share the same dynamic shape
(e.g. both are UUIDs, both are prefix + digits ids).  Values that carry
real categorical meaning (200 vs 500, GET vs POST) do not match any
dynamic template and are left untouched -- this keeps genuinely distinct
dimensions from being merged.
"""

from __future__ import annotations

import re

__all__ = ["TEMPLATES", "template_of", "extract_channel", "template_distribution"]

_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_PREFIX_DIGITS = re.compile(r"^([A-Za-z]{1,8})[-_]?(\d{6,})$")
_LONG_HEX = re.compile(r"^[0-9a-fA-F]{16,}$")
_IPV4 = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PURE_NUMBER = re.compile(r"^\d{9,}$")
_PATH_SEG = re.compile(r"/([^/?#]+)")

#: Ordered (name, regex, template) rules; first match wins.
TEMPLATES = [
    ("uuid", _UUID, "<uuid>"),
    ("email", _EMAIL, "<email>"),
    ("ipv4", _IPV4, "<ip>"),
    ("prefix_digits", _PREFIX_DIGITS, None),
    ("long_hex", _LONG_HEX, "<hex>"),
    ("pure_number", _PURE_NUMBER, "<num>"),
]


def template_of(value: str) -> str | None:
    """Return the template a value collapses to, or None if it is static."""
    for name, rx, tmpl in TEMPLATES:
        m = rx.match(value)
        if m:
            if name == "prefix_digits":
                return f"{m.group(1)}<digits>"
            return tmpl
    return None


def extract_channel(value: str) -> str | None:
    """Extract the meaningful prefix of an id such as ORD-EU-882134."""
    m = re.match(r"^([A-Za-z]{1,8}(?:-[A-Za-z]{1,8})*)-?\d{4,}$", value)
    return m.group(1) if m else None


def normalize_path(value: str) -> str | None:
    """Replace dynamic path segments with templates, keep static segments.

    /api/users/123/orders/ORD-991 -> /api/users/{id}/orders/{id}
    /api/health                   -> None (already static)
    """
    if "/" not in value:
        return None
    changed = False

    def _sub(m: re.Match) -> str:
        nonlocal changed
        seg = m.group(1)
        if (template_of(seg) is not None
                or re.fullmatch(r"\d+", seg)
                or re.fullmatch(r"[A-Za-z]{1,8}-?\d{3,}", seg)):
            changed = True
            return "/{id}"
        return m.group(0)

    out = _PATH_SEG.sub(_sub, value)
    return out if changed else None


def template_distribution(values) -> dict:
    """Map a sample of values to {template_or_'static': count}."""
    dist = {}
    for v in values:
        t = normalize_path(v)
        if t is None:
            ch = extract_channel(v)
            if ch is not None:
                t = f"{ch}<digits>"
        if t is None:
            t = template_of(v)
        t = t or "static"
        dist[t] = dist.get(t, 0) + 1
    return dist
