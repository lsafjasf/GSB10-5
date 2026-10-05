"""Display-width aware numeric column alignment using only the standard library."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import lru_cache
from typing import Any, Iterable, Sequence
import unicodedata


_FULLWIDTH_TRANSLATION = str.maketrans(
    {
        "０": "0",
        "１": "1",
        "２": "2",
        "３": "3",
        "４": "4",
        "５": "5",
        "６": "6",
        "７": "7",
        "８": "8",
        "９": "9",
        "．": ".",
        "。": ".",
        "，": ",",
        "＋": "+",
        "－": "-",
        "Ｅ": "E",
        "ｅ": "e",
        "−": "-",
        "﹣": "-",
    }
)


@dataclass(frozen=True)
class NumericFormat:
    """Controls canonical numeric formatting.

    Values whose adjusted decimal exponent is >= exponent_threshold or
    <= small_exponent_threshold use scientific notation. Other values use
    fixed-point notation with exactly decimal_places fractional digits.
    """

    decimal_places: int = 2
    use_thousands: bool = True
    exponent_threshold: int = 9
    small_exponent_threshold: int = -4

    def __post_init__(self) -> None:
        if self.decimal_places < 0:
            raise ValueError("decimal_places must be >= 0")
        if self.small_exponent_threshold >= 0:
            raise ValueError("small_exponent_threshold must be < 0")
        if self.exponent_threshold <= 0:
            raise ValueError("exponent_threshold must be > 0")


@dataclass
class AlignedTable:
    """Rendered table plus enough structure to verify display-cell offsets."""

    lines: list[str]
    cells: list[list[str]]
    column_starts: list[int]
    gap: int


def _is_variation_selector(char: str) -> bool:
    codepoint = ord(char)
    return 0xFE00 <= codepoint <= 0xFE0F or 0xE0100 <= codepoint <= 0xE01EF


def _is_emoji_modifier(char: str) -> bool:
    return 0x1F3FB <= ord(char) <= 0x1F3FF


def _is_keycap(char: str) -> bool:
    return ord(char) == 0x20E3


def _is_regional_indicator(char: str) -> bool:
    return 0x1F1E6 <= ord(char) <= 0x1F1FF


def _is_tag_character(char: str) -> bool:
    return 0xE0020 <= ord(char) <= 0xE007F


def _needs_clustering(char: str) -> bool:
    codepoint = ord(char)
    return (
        codepoint == 0x200D
        or codepoint == 0x0D
        or _is_variation_selector(char)
        or _is_emoji_modifier(char)
        or _is_keycap(char)
        or _is_tag_character(char)
        or _is_regional_indicator(char)
        or unicodedata.category(char) in {"Mn", "Me", "Mc"}
        or unicodedata.combining(char) != 0
    )


def _is_extend(char: str) -> bool:
    category = unicodedata.category(char)
    return (
        category in {"Mn", "Me", "Mc"}
        or unicodedata.combining(char) != 0
        or _is_variation_selector(char)
        or _is_emoji_modifier(char)
        or _is_keycap(char)
        or _is_tag_character(char)
    )


def _grapheme_clusters(text: str) -> tuple[str, ...]:
    """Return terminal-oriented, atomic grapheme-like clusters.

    This implements the cluster rules needed for robust table layout with the
    standard library: combining marks and modifiers attach to their base, ZWJ
    joins adjacent code points, regional indicators pair up, and CRLF stays
    together. It is intentionally not a full UAX #29 implementation.
    """

    clusters: list[str] = []
    regional_run = 0

    for char in text:
        if not clusters:
            clusters.append(char)
            regional_run = 1 if _is_regional_indicator(char) else 0
            continue

        previous = clusters[-1][-1]
        joins_previous = (
            char == "\u200d"
            or previous == "\u200d"
            or _is_extend(char)
            or (char == "\n" and previous == "\r")
            or (
                _is_regional_indicator(char)
                and _is_regional_indicator(previous)
                and regional_run % 2 == 1
            )
        )

        if joins_previous:
            clusters[-1] += char
        else:
            clusters.append(char)

        if _is_regional_indicator(char):
            regional_run += 1
        elif char != "\u200d" and not _is_extend(char):
            regional_run = 0

    return tuple(clusters)


@lru_cache(maxsize=65536)
def _cached_grapheme_clusters(text: str) -> tuple[str, ...]:
    return _grapheme_clusters(text)


def grapheme_clusters(text: str) -> list[str]:
    """Return terminal-oriented, atomic grapheme-like clusters."""

    return list(_cached_grapheme_clusters(str(text)))


@lru_cache(maxsize=4096)
def char_display_width(char: str) -> int:
    """Return the display width of one Unicode code point."""

    if len(char) != 1:
        raise ValueError("char_display_width expects exactly one code point")
    if char == "\t":
        return 4

    category = unicodedata.category(char)
    if (
        category in {"Mn", "Me", "Cf", "Cc"}
        or _is_variation_selector(char)
        or _is_emoji_modifier(char)
        or _is_keycap(char)
        or _is_tag_character(char)
    ):
        return 0
    if unicodedata.east_asian_width(char) in {"W", "F"}:
        return 2
    return 1


@lru_cache(maxsize=65536)
def cluster_display_width(cluster: str) -> int:
    """Return the terminal width of one atomic cluster."""

    if not cluster:
        return 0
    if (
        "\u200d" in cluster
        or any(_is_regional_indicator(char) for char in cluster)
        or any(_is_keycap(char) for char in cluster)
    ):
        return 2
    width = char_display_width(cluster[0])
    for char in cluster[1:]:
        if unicodedata.category(char) == "Mc":
            width += char_display_width(char)
        else:
            width = max(width, char_display_width(char))
    return width


@lru_cache(maxsize=65536)
def _display_width(text: str) -> int:
    if not any(_needs_clustering(char) for char in text):
        return sum(char_display_width(char) for char in text)
    return sum(cluster_display_width(cluster) for cluster in _cached_grapheme_clusters(text))


def display_width(text: Any) -> int:
    """Return the number of terminal display cells occupied by text."""

    return _display_width(str(text))


def truncate_display(text: Any, max_width: int) -> str:
    """Truncate to max_width without splitting an atomic cluster."""

    if max_width < 0:
        raise ValueError("max_width must be >= 0")

    output: list[str] = []
    used = 0
    for cluster in grapheme_clusters(str(text)):
        width = cluster_display_width(cluster)
        if used + width > max_width:
            break
        output.append(cluster)
        used += width
    return "".join(output)


def pad_right(text: Any, width: int) -> str:
    value = str(text)
    return value + " " * max(0, width - display_width(value))


def pad_left(text: Any, width: int) -> str:
    value = str(text)
    return " " * max(0, width - display_width(value)) + value


def _canonical_numeric_text(value: Any) -> str:
    if isinstance(value, bool):
        raise ValueError("booleans are not numeric cells")
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(value)
    if not isinstance(value, str):
        raise ValueError(f"unsupported numeric value type: {type(value).__name__}")

    text = value.strip().translate(_FULLWIDTH_TRANSLATION)
    if not text:
        raise ValueError("empty numeric value")
    return text.replace(",", "").replace("_", "")


def _format_exponent(exponent: int) -> str:
    sign = "+" if exponent >= 0 else "-"
    return f"E{sign}{abs(exponent):02d}"


@lru_cache(maxsize=65536)
def _normalize_number(canonical_text: str, fmt: NumericFormat) -> str:
    try:
        number = Decimal(canonical_text)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"not a finite decimal number: {canonical_text!r}") from exc
    if not number.is_finite():
        raise ValueError(f"not a finite decimal number: {value!r}")

    adjusted = number.adjusted() if number else 0
    use_scientific = bool(number) and (
        adjusted >= fmt.exponent_threshold or adjusted <= fmt.small_exponent_threshold
    )
    quantum = Decimal(1).scaleb(-fmt.decimal_places)

    if use_scientific:
        mantissa = number.scaleb(-adjusted).quantize(quantum, rounding=ROUND_HALF_UP)
        exponent = adjusted
        if mantissa == Decimal(10).quantize(quantum):
            mantissa = Decimal(1).quantize(quantum)
            exponent += 1
        return f"{mantissa:.{fmt.decimal_places}f}{_format_exponent(exponent)}"

    fixed = number.quantize(quantum, rounding=ROUND_HALF_UP)
    if fixed == 0:
        fixed = abs(fixed)
    if fmt.use_thousands:
        return f"{fixed:,.{fmt.decimal_places}f}"
    return f"{fixed:.{fmt.decimal_places}f}"


def normalize_number(value: Any, fmt: NumericFormat | None = None) -> str:
    """Canonicalize decimal places, grouping, minus sign, and exponent form."""

    fmt = fmt or NumericFormat()
    return _normalize_number(_canonical_numeric_text(value), fmt)


def is_numeric_cell(value: Any, fmt: NumericFormat | None = None) -> bool:
    try:
        normalize_number(value, fmt)
    except (ValueError, TypeError):
        return False
    return True


def clear_caches() -> None:
    """Clear internal layout and normalization caches."""

    _cached_grapheme_clusters.cache_clear()
    char_display_width.cache_clear()
    cluster_display_width.cache_clear()
    _display_width.cache_clear()
    _normalize_number.cache_clear()


def _split_normalized_number(text: str) -> tuple[str, str, str]:
    exponent = ""
    mantissa = text
    if "E" in text:
        mantissa, exponent_part = text.split("E", 1)
        exponent = "E" + exponent_part

    if "." in mantissa:
        left, fraction = mantissa.split(".", 1)
        fraction = "." + fraction
    else:
        left, fraction = mantissa, ""
    return left, fraction, exponent


def align_columns(
    rows: Iterable[Sequence[Any]],
    *,
    gap: int = 2,
    numeric_format: NumericFormat | None = None,
) -> AlignedTable:
    """Normalize numeric cells and align rectangular rows by display width.

    Numeric cells are aligned internally at their decimal point and exponent.
    Non-numeric cells are left-aligned. All padding uses terminal cells, not
    Python code-point counts.
    """

    if gap < 0:
        raise ValueError("gap must be >= 0")
    fmt = numeric_format or NumericFormat()
    source_rows = [list(row) for row in rows]
    if not source_rows:
        return AlignedTable([], [], [], gap)

    column_count = len(source_rows[0])
    if column_count == 0:
        raise ValueError("rows must contain at least one column")
    if any(len(row) != column_count for row in source_rows):
        raise ValueError("rows must be rectangular")

    normalized: list[list[str]] = []
    numeric_mask: list[list[bool]] = []
    for row in source_rows:
        normalized_row: list[str] = []
        numeric_row: list[bool] = []
        for value in row:
            try:
                normalized_row.append(normalize_number(value, fmt))
                numeric_row.append(True)
            except (ValueError, TypeError):
                normalized_row.append(str(value))
                numeric_row.append(False)
        normalized.append(normalized_row)
        numeric_mask.append(numeric_row)

    column_widths = [0] * column_count
    numeric_left_widths = [0] * column_count
    numeric_fraction_widths = [0] * column_count
    numeric_exponent_widths = [0] * column_count

    for column in range(column_count):
        for row_index, row in enumerate(normalized):
            value = row[column]
            if numeric_mask[row_index][column]:
                left, fraction, exponent = _split_normalized_number(value)
                numeric_left_widths[column] = max(
                    numeric_left_widths[column], display_width(left)
                )
                numeric_fraction_widths[column] = max(
                    numeric_fraction_widths[column], display_width(fraction)
                )
                numeric_exponent_widths[column] = max(
                    numeric_exponent_widths[column], display_width(exponent)
                )
            else:
                column_widths[column] = max(column_widths[column], display_width(value))

        numeric_width = (
            numeric_left_widths[column]
            + numeric_fraction_widths[column]
            + numeric_exponent_widths[column]
        )
        column_widths[column] = max(column_widths[column], numeric_width)

    rendered_rows: list[list[str]] = []
    for row_index, row in enumerate(normalized):
        rendered_row: list[str] = []
        for column, value in enumerate(row):
            if numeric_mask[row_index][column]:
                left, fraction, exponent = _split_normalized_number(value)
                numeric_block = (
                    pad_left(left, numeric_left_widths[column])
                    + pad_right(fraction, numeric_fraction_widths[column])
                    + pad_right(exponent, numeric_exponent_widths[column])
                )
                rendered_row.append(pad_left(numeric_block, column_widths[column]))
            else:
                rendered_row.append(pad_right(value, column_widths[column]))
        rendered_rows.append(rendered_row)

    column_starts: list[int] = []
    offset = 0
    for width in column_widths:
        column_starts.append(offset)
        offset += width + gap

    separator = " " * gap
    lines = [separator.join(row).rstrip() for row in rendered_rows]
    return AlignedTable(lines, rendered_rows, column_starts, gap)


def verify_column_starts(table: AlignedTable) -> list[tuple[int, int, int]]:
    """Assert every row/column prefix starts at the declared display offset."""

    checks: list[tuple[int, int, int]] = []
    for row_index, row in enumerate(table.cells):
        prefix = ""
        for column, cell in enumerate(row):
            expected = table.column_starts[column]
            actual = display_width(prefix)
            assert actual == expected, (
                f"row {row_index}, column {column}: expected start "
                f"{expected}, got {actual}"
            )
            checks.append((row_index, column, expected))
            prefix += cell
            if column + 1 < len(row):
                prefix += " " * table.gap
    return checks
