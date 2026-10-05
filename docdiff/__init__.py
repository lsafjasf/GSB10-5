"""docdiff: 面向 Markdown 文档的版本结构差异库（仅标准库）。

公开 API:
    diff_documents(old_text, new_text, threshold=DEFAULT_THRESHOLD) -> DiffResult
    diff_files(old_path, new_path, ...) -> DiffResult
    summarize(diff) / expand_summary(summary) / verify_roundtrip(diff)
    sweep_thresholds(old_text, new_text, thresholds) -> list[dict]
"""

from .model import Section, SectionIndex, parse_document
from .match import Matcher, MatchResult, DEFAULT_THRESHOLD, CONTENT_WEIGHT, TITLE_WEIGHT
from .diff import (
    Change,
    DiffResult,
    diff_documents,
    diff_files,
    summarize,
    expand_summary,
    verify_roundtrip,
    sweep_thresholds,
    render_markdown,
)

__all__ = [
    "Section", "SectionIndex", "parse_document",
    "Matcher", "MatchResult", "DEFAULT_THRESHOLD", "CONTENT_WEIGHT", "TITLE_WEIGHT",
    "Change", "DiffResult", "diff_documents", "diff_files",
    "summarize", "expand_summary", "verify_roundtrip", "sweep_thresholds",
    "render_markdown",
]
