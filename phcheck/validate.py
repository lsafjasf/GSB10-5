"""译文占位符校验：按多重集（集合 + 重复次数）比对，与顺序无关。"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List

from .tokens import scan


@dataclass(frozen=True)
class Diff:
    ident: str
    source: int
    translation: int

    @property
    def delta(self) -> int:
        return self.translation - self.source

    def as_dict(self) -> dict:
        return {
            "ident": self.ident,
            "source": self.source,
            "translation": self.translation,
            "delta": self.delta,
        }


@dataclass(frozen=True)
class FormatDrift:
    ident: str
    source_spec: str
    translation_spec: str

    def as_dict(self) -> dict:
        return {
            "ident": self.ident,
            "source_spec": self.source_spec,
            "translation_spec": self.translation_spec,
        }


@dataclass
class Report:
    missing: List[Diff] = field(default_factory=list)
    extra: List[Diff] = field(default_factory=list)
    count_mismatch: List[Diff] = field(default_factory=list)
    invalid: List[dict] = field(default_factory=list)
    format_drift: List[FormatDrift] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.missing or self.extra or self.count_mismatch or self.invalid)

    @property
    def categories(self) -> List[str]:
        cats = []
        if self.invalid:
            cats.append("invalid")
        if self.missing:
            cats.append("missing")
        if self.extra:
            cats.append("extra")
        if self.count_mismatch:
            cats.append("count_mismatch")
        if self.format_drift:
            cats.append("format_drift")
        return cats

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "missing": [d.as_dict() for d in self.missing],
            "extra": [d.as_dict() for d in self.extra],
            "count_mismatch": [d.as_dict() for d in self.count_mismatch],
            "invalid": list(self.invalid),
            "format_drift": [d.as_dict() for d in self.format_drift],
        }


def _spec_map(tokens) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for tok in tokens:
        key = tok.spec_key
        if tok.ident not in result or key < result[tok.ident]:
            result[tok.ident] = key
    return result


def validate(source: str, translation: str, strict_format: bool = False) -> Report:
    """校验译文的占位符多重集是否与原文一致（与顺序无关）。

    分类规则：
    - missing        原文有、译文没有的占位符身份
    - extra          译文有、原文没有的占位符身份
    - count_mismatch 两边都有、但重复次数不一致
    - invalid        任一侧存在非法占位符结构
    - format_drift   （可选，strict_format=True）身份与次数一致但宽度/精度/标志/长度不同
    """
    src = scan(source)
    tgt = scan(translation)
    invalid = [
        {"where": "source", "start": it.start, "text": it.text, "reason": it.reason}
        for it in src.invalid
    ] + [
        {"where": "translation", "start": it.start, "text": it.text, "reason": it.reason}
        for it in tgt.invalid
    ]
    src_counter = Counter(t.ident for t in src.tokens)
    tgt_counter = Counter(t.ident for t in tgt.tokens)
    missing: List[Diff] = []
    extra: List[Diff] = []
    mismatch: List[Diff] = []
    for ident in sorted(set(src_counter) | set(tgt_counter)):
        s = src_counter.get(ident, 0)
        t = tgt_counter.get(ident, 0)
        if s == t:
            continue
        diff = Diff(ident, s, t)
        if s == 0:
            extra.append(diff)
        elif t == 0:
            missing.append(diff)
        else:
            mismatch.append(diff)
    drift: List[FormatDrift] = []
    if strict_format:
        src_spec = _spec_map(src.tokens)
        tgt_spec = _spec_map(tgt.tokens)
        for ident in sorted(set(src_spec) & set(tgt_spec)):
            if src_counter[ident] != tgt_counter[ident]:
                continue
            if src_spec[ident] != tgt_spec[ident]:
                drift.append(FormatDrift(ident, src_spec[ident], tgt_spec[ident]))
    return Report(missing, extra, mismatch, invalid, drift)
