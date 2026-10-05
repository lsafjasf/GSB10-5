"""Renumber numeric citations by first appearance in the body.

The supported document format is intentionally small and explicit:

* The body ends at a line matching the references heading (default:
  ``## 参考文献``).
* A citation in the body is written as ``[n]`` where ``n`` is a positive
  integer.
* A bibliography entry starts at the beginning of a line with ``[n]``.
  The entry continues until the next bibliography label or the end of the
  document.

Only the Python standard library is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


DEFAULT_HEADING = "## 参考文献"
CITATION_RE = re.compile(r"\[([1-9][0-9]*)\]")
ENTRY_RE = re.compile(r"(?m)^\[([1-9][0-9]*)\]")
NORMALIZED_CITATION_PLACEHOLDER = "\x00CITATION\x00"


@dataclass(frozen=True)
class CitationOccurrence:
    """One citation marker in the body."""

    number: int
    start: int
    end: int


@dataclass(frozen=True)
class BibliographyEntry:
    """One bibliography entry in the references section."""

    number: int
    order: int
    start: int
    end: int
    label_end: int
    text: str

    @property
    def suffix(self) -> str:
        """Entry text after its numeric label."""

        return self.text[self.label_end - self.start :]


@dataclass(frozen=True)
class DocumentParts:
    body: str
    heading: str
    references: str
    heading_start: int
    heading_end: int


@dataclass(frozen=True)
class Analysis:
    """Deterministic analysis of citations and bibliography entries."""

    citations: Tuple[CitationOccurrence, ...]
    entries: Tuple[BibliographyEntry, ...]
    mapping: Mapping[int, int]
    missing_entries: Tuple[int, ...]
    orphan_entries: Tuple[int, ...]
    numbering_gaps: Tuple[int, ...]
    duplicate_entry_numbers: Tuple[int, ...]

    @property
    def citation_numbers(self) -> List[int]:
        return [citation.number for citation in self.citations]

    def diagnostics(self) -> Dict[str, Any]:
        return {
            "citation_points": len(self.citations),
            "unique_cited_entries": len({c.number for c in self.citations}),
            "bibliography_entries": len(self.entries),
            "missing_entries": list(self.missing_entries),
            "orphan_entries": list(self.orphan_entries),
            "numbering_gaps": list(self.numbering_gaps),
            "duplicate_entry_numbers": list(self.duplicate_entry_numbers),
        }

    def mapping_table(self) -> List[Dict[str, Any]]:
        first_indexes: Dict[int, int] = {}
        for index, citation in enumerate(self.citations):
            first_indexes.setdefault(citation.number, index)

        table: List[Dict[str, Any]] = []
        for old_number, new_number in sorted(
            self.mapping.items(), key=lambda item: item[1]
        ):
            first_index = first_indexes.get(old_number)
            table.append(
                {
                    "old": old_number,
                    "new": new_number,
                    "status": "cited" if first_index is not None else "orphan",
                    "first_citation_index": first_index,
                }
            )
        return table


@dataclass(frozen=True)
class RewriteResult:
    """Rewritten document plus machine-checkable validation data."""

    text: str
    analysis: Analysis
    consistency: Mapping[str, Any]

    def report(self) -> Dict[str, Any]:
        return {
            "numbering_rules": {
                "cited_entries": "按正文中首次出现的顺序从 1 连续编号",
                "repeated_citations": "同一条目无论被引用多少次都使用同一个新编号",
                "orphan_entries": "未被正文引用的条目排在已引用条目之后，按原列表顺序继续连续编号",
                "missing_entries": "正文引用但文末不存在的编号不参与重排；默认阻止回写",
            },
            "diagnostics": self.analysis.diagnostics(),
            "mapping_table": self.analysis.mapping_table(),
            "consistency": self.consistency,
        }


class CitationError(ValueError):
    """Raised when a document cannot be safely rewritten."""

    def __init__(self, message: str, analysis: Optional[Analysis] = None):
        super().__init__(message)
        self.analysis = analysis


def split_document(
    document: str, references_heading: str = DEFAULT_HEADING
) -> DocumentParts:
    """Split a document into body, heading, and references section."""

    heading_re = re.compile(
        rf"(?m)^[ \t]*{re.escape(references_heading)}[ \t]*$"
    )
    match = heading_re.search(document)
    if match is None:
        raise CitationError(f"找不到参考文献标题行: {references_heading!r}")
    return DocumentParts(
        body=document[: match.start()],
        heading=document[match.start() : match.end()],
        references=document[match.end() :],
        heading_start=match.start(),
        heading_end=match.end(),
    )


def _parse_entries(references: str) -> Tuple[BibliographyEntry, ...]:
    matches = list(ENTRY_RE.finditer(references))
    entries: List[BibliographyEntry] = []
    for order, match in enumerate(matches):
        end = matches[order + 1].start() if order + 1 < len(matches) else len(references)
        entries.append(
            BibliographyEntry(
                number=int(match.group(1)),
                order=order,
                start=match.start(),
                end=end,
                label_end=match.end(),
                text=references[match.start() : end],
            )
        )
    return tuple(entries)


def _numbering_gaps(numbers: Iterable[int]) -> Tuple[int, ...]:
    number_set = set(numbers)
    if not number_set:
        return ()
    return tuple(number for number in range(1, max(number_set) + 1) if number not in number_set)


def analyze_document(
    document: str, references_heading: str = DEFAULT_HEADING
) -> Analysis:
    """Analyze a document and compute the deterministic old-to-new mapping."""

    parts = split_document(document, references_heading)
    citations = tuple(
        CitationOccurrence(
            number=int(match.group(1)), start=match.start(), end=match.end()
        )
        for match in CITATION_RE.finditer(parts.body)
    )
    entries = _parse_entries(parts.references)

    entry_numbers = [entry.number for entry in entries]
    entry_number_set = set(entry_numbers)
    entry_number_counts = Counter(entry_numbers)
    duplicate_numbers = tuple(
        sorted(
            number
            for number, count in entry_number_counts.items()
            if count > 1
        )
    )

    cited_numbers = {citation.number for citation in citations}
    missing_entries = tuple(sorted(cited_numbers - entry_number_set))
    orphan_entries = tuple(
        entry.number for entry in entries if entry.number not in cited_numbers
    )

    mapping: Dict[int, int] = {}
    next_number = 1
    seen_cited: set[int] = set()
    for citation in citations:
        number = citation.number
        if number in entry_number_set and number not in seen_cited:
            mapping[number] = next_number
            next_number += 1
            seen_cited.add(number)

    for entry in entries:
        if entry.number not in mapping and entry.number not in duplicate_numbers:
            mapping[entry.number] = next_number
            next_number += 1

    return Analysis(
        citations=citations,
        entries=entries,
        mapping=mapping,
        missing_entries=missing_entries,
        orphan_entries=orphan_entries,
        numbering_gaps=_numbering_gaps(entry_number_set),
        duplicate_entry_numbers=duplicate_numbers,
    )


def _replace_body_citations(body: str, mapping: Mapping[int, int]) -> str:
    pieces: List[str] = []
    last_end = 0
    for match in CITATION_RE.finditer(body):
        old_number = int(match.group(1))
        pieces.append(body[last_end : match.start()])
        pieces.append(f"[{mapping.get(old_number, old_number)}]")
        last_end = match.end()
    pieces.append(body[last_end:])
    return "".join(pieces)


def _rebuild_references(
    references: str, entries: Sequence[BibliographyEntry], mapping: Mapping[int, int]
) -> str:
    if not entries:
        return references

    first_start = entries[0].start
    prefix = references[:first_start]
    ordered_entries = sorted(entries, key=lambda entry: mapping[entry.number])
    rebuilt = [prefix]
    for entry in ordered_entries:
        new_number = mapping[entry.number]
        rebuilt.append(f"[{new_number}]{entry.suffix}")
    return "".join(rebuilt)


def _normalized_body_bytes(body: str) -> bytes:
    normalized = CITATION_RE.sub(NORMALIZED_CITATION_PLACEHOLDER, body)
    return normalized.encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _first_byte_difference(left: bytes, right: bytes) -> Optional[int]:
    for index, (left_byte, right_byte) in enumerate(zip(left, right)):
        if left_byte != right_byte:
            return index
    if len(left) != len(right):
        return min(len(left), len(right))
    return None


def _validate_rewrite(
    original: str,
    rewritten: str,
    analysis: Analysis,
    references_heading: str,
) -> Dict[str, Any]:
    original_parts = split_document(original, references_heading)
    rewritten_parts = split_document(rewritten, references_heading)
    rewritten_analysis = analyze_document(rewritten, references_heading)

    original_normalized = _normalized_body_bytes(original_parts.body)
    rewritten_normalized = _normalized_body_bytes(rewritten_parts.body)
    body_equal = original_normalized == rewritten_normalized

    expected_sequence = [
        analysis.mapping.get(citation.number, citation.number)
        for citation in analysis.citations
    ]
    actual_sequence = rewritten_analysis.citation_numbers

    original_entries = {entry.number: entry for entry in analysis.entries}
    rewritten_entries = {entry.number: entry for entry in rewritten_analysis.entries}
    entry_suffixes_preserved = all(
        rewritten_entries[new_number].suffix == original_entries[old_number].suffix
        for old_number, new_number in analysis.mapping.items()
        if old_number in original_entries and new_number in rewritten_entries
    )

    cited_new_numbers = []
    seen = set()
    for number in actual_sequence:
        if number not in seen:
            cited_new_numbers.append(number)
            seen.add(number)

    reference_numbers = [entry.number for entry in rewritten_analysis.entries]
    expected_reference_numbers = list(range(1, len(reference_numbers) + 1))
    mapping_round_trip = all(
        old_number in analysis.mapping
        and analysis.mapping[old_number] in rewritten_entries
        for old_number in original_entries
    )

    return {
        "body_content_byte_comparison": {
            "method": "正文中每个 [n] 引用标记先替换为固定占位符，再对 UTF-8 字节逐字节比较",
            "equal": body_equal,
            "compared_bytes": len(original_normalized),
            "first_difference_byte": _first_byte_difference(
                original_normalized, rewritten_normalized
            ),
            "original_sha256": _sha256(original_normalized),
            "rewritten_sha256": _sha256(rewritten_normalized),
        },
        "raw_body_bytes": {
            "equal": original_parts.body.encode("utf-8")
            == rewritten_parts.body.encode("utf-8"),
            "original_sha256": _sha256(original_parts.body.encode("utf-8")),
            "rewritten_sha256": _sha256(rewritten_parts.body.encode("utf-8")),
            "note": "原始字节可因引用编号位数变化而不同；正文非引用内容以 normalized 比较为准",
        },
        "citation_sequence": {
            "equal": actual_sequence == expected_sequence,
            "citation_points": len(actual_sequence),
            "expected_first_20": expected_sequence[:20],
            "actual_first_20": actual_sequence[:20],
        },
        "reference_list": {
            "numbers_are_contiguous": reference_numbers == expected_reference_numbers,
            "numbers": reference_numbers,
            "cited_numbers_in_first_appearance_order": cited_new_numbers,
            "entry_suffixes_preserved": entry_suffixes_preserved,
            "mapping_round_trip": mapping_round_trip,
            "all_body_citations_resolve": not rewritten_analysis.missing_entries,
            "orphan_entries": list(rewritten_analysis.orphan_entries),
        },
    }


def renumber_document(
    document: str,
    references_heading: str = DEFAULT_HEADING,
    *,
    allow_missing: bool = False,
) -> RewriteResult:
    """Renumber citations and reorder bibliography entries.

    By default, a citation to a nonexistent bibliography entry is an error,
    because leaving that marker unchanged could collide with a newly assigned
    number. Pass ``allow_missing=True`` only when the caller explicitly wants
    a diagnostic rewrite that leaves unresolved markers unchanged.
    """

    analysis = analyze_document(document, references_heading)
    if analysis.duplicate_entry_numbers:
        raise CitationError(
            "文末条目编号重复: " + ", ".join(map(str, analysis.duplicate_entry_numbers)),
            analysis,
        )
    if analysis.missing_entries and not allow_missing:
        raise CitationError(
            "正文引用了不存在的条目: " + ", ".join(map(str, analysis.missing_entries)),
            analysis,
        )

    parts = split_document(document, references_heading)
    rewritten_body = _replace_body_citations(parts.body, analysis.mapping)
    rewritten_references = _rebuild_references(
        parts.references, analysis.entries, analysis.mapping
    )
    rewritten = rewritten_body + parts.heading + rewritten_references
    consistency = _validate_rewrite(
        document, rewritten, analysis, references_heading
    )
    return RewriteResult(text=rewritten, analysis=analysis, consistency=consistency)


def _write_json(data: Mapping[str, Any], path: Optional[str]) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if path:
        Path(path).write_text(payload, encoding="utf-8")
    else:
        sys.stderr.write(payload)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="按正文首次出现顺序重排数字引用和文末条目"
    )
    parser.add_argument("input", help="输入 Markdown/文本文件（UTF-8）")
    parser.add_argument("-o", "--output", help="回写后的输出文件；省略时写到 stdout")
    parser.add_argument("--report", help="JSON 校验报告输出路径；省略时写到 stderr")
    parser.add_argument(
        "--heading",
        default=DEFAULT_HEADING,
        help=f"参考文献标题行，默认 {DEFAULT_HEADING!r}",
    )
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="允许正文引用缺失条目并保留这些标记（默认视为错误）",
    )
    args = parser.parse_args(argv)

    document = Path(args.input).read_text(encoding="utf-8")
    try:
        result = renumber_document(
            document, args.heading, allow_missing=args.allow_missing
        )
    except CitationError as exc:
        report: Dict[str, Any] = {"error": str(exc)}
        if exc.analysis is not None:
            report["diagnostics"] = exc.analysis.diagnostics()
            report["mapping_table"] = exc.analysis.mapping_table()
        _write_json(report, args.report)
        return 2

    if args.output:
        Path(args.output).write_text(result.text, encoding="utf-8")
    else:
        sys.stdout.write(result.text)
    _write_json(result.report(), args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
