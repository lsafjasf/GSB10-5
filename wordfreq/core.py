from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import unicodedata
from typing import Iterator, Mapping


_CJK_RANGES = (
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0x20000, 0x2A6DF),
    (0x2A700, 0x2B73F),
    (0x2B740, 0x2B81F),
    (0x2B820, 0x2CEAF),
    (0x2CEB0, 0x2EBEF),
    (0x30000, 0x3134F),
    (0x31350, 0x323AF),
)

_HYPHENS = {"-", "\u2010"}
_APOSTROPHES = {"'", "\u2019"}


@dataclass(frozen=True)
class TokenizationProfile:
    """Declarative tokenization settings."""

    name: str
    case: str = "lower"
    hyphen: str = "split"
    apostrophe: str = "join"
    cjk: str = "char"
    unicode_normalization: str = "NFKC"

    def __post_init__(self) -> None:
        if self.case not in {"lower", "preserve"}:
            raise ValueError("case must be 'lower' or 'preserve'")
        if self.hyphen not in {"split", "join"}:
            raise ValueError("hyphen must be 'split' or 'join'")
        if self.apostrophe not in {"split", "join"}:
            raise ValueError("apostrophe must be 'split' or 'join'")
        if self.cjk != "char":
            raise ValueError("cjk currently must be 'char'")
        if self.unicode_normalization != "NFKC":
            raise ValueError("unicode_normalization currently must be 'NFKC'")


UNICODE_V1 = TokenizationProfile(
    name="unicode-v1",
    case="lower",
    hyphen="split",
    apostrophe="join",
)

LOOSE_V1 = TokenizationProfile(
    name="loose-v1",
    case="preserve",
    hyphen="join",
    apostrophe="join",
)

PROFILES = {
    UNICODE_V1.name: UNICODE_V1,
    LOOSE_V1.name: LOOSE_V1,
}


def _is_cjk_ideograph(char: str) -> bool:
    codepoint = ord(char)
    return any(start <= codepoint <= end for start, end in _CJK_RANGES)


def _is_word_start(char: str) -> bool:
    return unicodedata.category(char)[0] in {"L", "N"}


def _is_word_continue(char: str) -> bool:
    return unicodedata.category(char)[0] in {"L", "N", "M"}


def _normalize(text: str, profile: TokenizationProfile) -> str:
    normalized = unicodedata.normalize(profile.unicode_normalization, text)
    if profile.case == "lower":
        return normalized.casefold()
    return normalized


def tokenize(
    text: str,
    profile: TokenizationProfile = UNICODE_V1,
) -> Iterator[str]:
    """Yield tokens from text according to profile.

    This is a one-pass generator. It does not build an intermediate token list;
    peak memory is driven by Unicode normalization copies and unique tokens,
    not by the total token count.
    """

    if not isinstance(text, str):
        raise TypeError("text must be str")

    normalized = _normalize(text, profile)
    length = len(normalized)
    index = 0

    while index < length:
        char = normalized[index]

        if _is_cjk_ideograph(char):
            yield char
            index += 1
            continue

        if not _is_word_start(char):
            index += 1
            continue

        parts = [char]
        index += 1

        while index < length:
            current = normalized[index]

            if _is_cjk_ideograph(current):
                break

            if _is_word_continue(current):
                parts.append(current)
                index += 1
                continue

            connector_kind = None
            if current in _HYPHENS and profile.hyphen == "join":
                connector_kind = "hyphen"
            elif current in _APOSTROPHES and profile.apostrophe == "join":
                connector_kind = "apostrophe"

            if connector_kind is None:
                break

            next_index = index + 1
            previous_is_word = _is_word_continue(parts[-1])
            next_is_word = (
                next_index < length
                and not _is_cjk_ideograph(normalized[next_index])
                and _is_word_start(normalized[next_index])
            )
            if not (previous_is_word and next_is_word):
                break

            parts.append(current)
            index += 1

        yield "".join(parts)


def count_tokens(
    text: str,
    profile: TokenizationProfile = UNICODE_V1,
) -> Counter[str]:
    """Count tokens in one pass."""

    return Counter(tokenize(text, profile))


def sorted_counts(counts: Mapping[str, int]) -> list[tuple[str, int]]:
    """Return a deterministic count ordering: count descending, then token."""

    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def count_summary(
    text: str,
    profile: TokenizationProfile = UNICODE_V1,
) -> dict[str, object]:
    """Return a JSON-serializable, deterministic count summary."""

    counts = count_tokens(text, profile)
    items = sorted_counts(counts)
    return {
        "profile": profile.name,
        "total_tokens": sum(counts.values()),
        "unique_tokens": len(counts),
        "counts": [{"token": token, "count": count} for token, count in items],
    }
