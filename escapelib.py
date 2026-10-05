"""Context-aware output escaping / injection defense (Python 3, stdlib only).

Core idea: the SAME user string needs DIFFERENT escaping depending on where
it lands in the output. One-size-fits-all escaping always leaks somewhere.

Contexts covered:
    text   - HTML text nodes
    attr   - HTML attribute values (always quoted)
    js     - JS string literal inside <script>
    css    - CSS value inside <style> / style=""
    url    - href/src addresses (percent-encoding + scheme allowlist)

Nested contexts (composition):
    url_in_attr  - <a href="URL">
    url_in_css   - CSS url("URL")
    css_in_attr  - style="CSS"
    json_in_script - <script>var d = JSON</script>

Idempotency: every escape_* returns a SafeString tagged with its context.
Re-escaping for the SAME context is a no-op, so already-escaped content is
never double-escaped into mojibake (&amp;amp; etc.). Escaping for a
DIFFERENT context still applies (that is composition, not double escaping).
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote, urlsplit

__all__ = [
    "SafeString",
    "UnsafeUrlError",
    "escape_text",
    "escape_attr",
    "escape_js_string",
    "escape_css",
    "escape_url",
    "escape_url_in_attr",
    "escape_css_url",
    "escape_css_in_attr",
    "escape_json_in_script",
]


class SafeString(str):
    """A string already escaped for a specific output context."""

    def __new__(cls, value: str, context: str) -> "SafeString":
        obj = super().__new__(cls, value)
        obj._context = context
        return obj

    @property
    def context(self) -> str:
        return self._context


def _already(value, context: str):
    """Return the SafeString unchanged if already escaped for `context`."""
    if isinstance(value, SafeString) and value.context == context:
        return value
    return None


# --------------------------------------------------------------------------
# 1. HTML text context:  <p>HERE</p>
#    Rule: & < > must become entities. Quotes are harmless in text but we
#    leave them alone to keep output readable.
# --------------------------------------------------------------------------
_TEXT_TABLE = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;"})


def escape_text(value) -> SafeString:
    done = _already(value, "text")
    if done is not None:
        return done
    return SafeString(str(value).translate(_TEXT_TABLE), "text")


# --------------------------------------------------------------------------
# 2. HTML attribute context:  <div title="HERE">
#    Rule: additionally escape both quote chars so the value can never
#    terminate its quotes. Attributes MUST be quoted in the template.
# --------------------------------------------------------------------------
_ATTR_TABLE = str.maketrans(
    {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#x27;",
    }
)


def escape_attr(value) -> SafeString:
    done = _already(value, "attr")
    if done is not None:
        return done
    return SafeString(str(value).translate(_ATTR_TABLE), "attr")


# --------------------------------------------------------------------------
# 3. JS string context:  <script>var s = "HERE";</script>
#    Rule: backslash-escape \, both quotes and control chars; escape < as
#    \x3C so the sequence </script> can never appear; escape U+2028/U+2029
#    (valid JS line terminators in older engines).
# --------------------------------------------------------------------------
_JS_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "'": "\\'",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\b": "\\b",
    "\f": "\\f",
    "\v": "\\u000B",
    "\0": "\\0",
    "<": "\\x3C",
    ">": "\\x3E",
    "\u2028": "\\u2028",
    "\u2029": "\\u2029",
}


def _escape_js_char(ch: str) -> str:
    esc = _JS_ESCAPES.get(ch)
    if esc is not None:
        return esc
    code = ord(ch)
    if code < 0x20 or code == 0x7F:
        return "\\x%02X" % code
    return ch


def escape_js_string(value) -> SafeString:
    """Escape for embedding inside a quoted JS string literal in <script>."""
    done = _already(value, "js")
    if done is not None:
        return done
    return SafeString("".join(_escape_js_char(c) for c in str(value)), "js")


# --------------------------------------------------------------------------
# 4. CSS context:  <style>a { color: HERE }</style>  or style="HERE"
#    Rule: every char outside [A-Za-z0-9] (and non-ASCII, which CSS allows)
#    becomes a backslash-hex escape with trailing space, e.g. } -> \7D .
#    This neutralizes </style> breakout, braces, quotes, semicolons, etc.
# --------------------------------------------------------------------------
_CSS_SAFE = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
)


def _escape_css_char(ch: str) -> str:
    if ch in _CSS_SAFE or ord(ch) >= 0x80:
        return ch
    return "\\%02X " % ord(ch)


def escape_css(value) -> SafeString:
    done = _already(value, "css")
    if done is not None:
        return done
    return SafeString("".join(_escape_css_char(c) for c in str(value)), "css")


# --------------------------------------------------------------------------
# 5. URL context:  <a href="HERE">
#    Rule: strip \t \n \r (browsers ignore them inside schemes), reject
#    schemes outside the allowlist (blocks javascript:, data:, vbscript:),
#    then percent-encode everything outside the RFC 3986 reserved+unreserved
#    set. Relative URLs (no scheme) are allowed.
# --------------------------------------------------------------------------
DEFAULT_ALLOWED_SCHEMES = frozenset({"http", "https", "mailto", "tel", "ftp"})

_URL_STRIP = re.compile(r"[\t\n\r]")
_URL_SAFE = ":/?#[]@!$&'()*+,;=-._~%"


class UnsafeUrlError(ValueError):
    pass


def escape_url(value, allowed_schemes=DEFAULT_ALLOWED_SCHEMES) -> SafeString:
    done = _already(value, "url")
    if done is not None:
        return done
    raw = _URL_STRIP.sub("", str(value)).strip()
    # Control chars / spaces must not survive; quote() handles them, but a
    # scheme check needs a clean view first.
    probe = raw.lstrip("".join(chr(c) for c in range(0x21)) + "\x7f")
    scheme = urlsplit(probe).scheme.lower()
    if scheme and scheme not in allowed_schemes:
        raise UnsafeUrlError(f"disallowed URL scheme: {scheme!r}")
    return SafeString(quote(raw, safe=_URL_SAFE), "url")


# --------------------------------------------------------------------------
# Nested contexts: apply inner escaping first, then outer.
# --------------------------------------------------------------------------

def escape_url_in_attr(value, allowed_schemes=DEFAULT_ALLOWED_SCHEMES) -> SafeString:
    """<a href="VALUE"> : URL rules first, then attribute rules."""
    return escape_attr(escape_url(value, allowed_schemes))


def escape_css_url(value, allowed_schemes=DEFAULT_ALLOWED_SCHEMES) -> SafeString:
    """CSS url("VALUE") : stricter percent-encoding (no parens/quotes/backslash
    survive), then the result is safe inside a CSS string token."""
    raw = _URL_STRIP.sub("", str(value)).strip()
    probe = raw.lstrip("".join(chr(c) for c in range(0x21)) + "\x7f")
    scheme = urlsplit(probe).scheme.lower()
    if scheme and scheme not in allowed_schemes:
        raise UnsafeUrlError(f"disallowed URL scheme: {scheme!r}")
    # Note: ( ) " ' \ and space are NOT in the safe set here.
    encoded = quote(raw, safe=":/?#[]@!$&*+,;=-._~%")
    return SafeString(encoded, "css_url")


def escape_css_in_attr(value) -> SafeString:
    """style="VALUE" : CSS rules first, then attribute rules."""
    return escape_attr(escape_css(value))


def escape_json_in_script(value) -> SafeString:
    """<script>var d = VALUE;</script> : JSON-encode, then neutralize the
    three sequences that can break out of <script> or old JS parsers."""
    done = _already(value, "json_script")
    if done is not None:
        return done
    text = json.dumps(value, ensure_ascii=False)
    text = (
        text.replace("<", "\\u003C")
        .replace(">", "\\u003E")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    return SafeString(text, "json_script")
