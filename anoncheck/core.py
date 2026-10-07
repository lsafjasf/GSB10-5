"""Core re-identification risk analysis (k-anonymity style).

Streaming two-pass design:

* pass 1 counts equivalence classes over the chosen quasi-identifier (QI)
  tuple -- memory is proportional to the number of *distinct* QI
  combinations, not to the number of records;
* pass 2 (optional) locates the concrete records that fall into risky
  classes, so findings are auditable row by row.
"""

from __future__ import annotations

import csv
import itertools
import statistics
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, Iterator, List, Optional, Sequence, Tuple

Transform = Callable[[str], str]

# Separator inside a class key. Unit-separator cannot appear in CSV text
# produced by common exporters, and transforms never emit it.
SEP = "\x1f"

_HISTOGRAM_BUCKETS = (
    ("1", 1, 1),
    ("2", 2, 2),
    ("3-4", 3, 4),
    ("5-9", 5, 9),
    ("10-49", 10, 49),
    ("50+", 50, None),
)


def _identity(value: str) -> str:
    return value


def _normalize_transforms(
    fields: Sequence[str], transforms: Optional[Sequence[Transform]]
) -> List[Transform]:
    if transforms is None:
        return [_identity] * len(fields)
    if len(transforms) != len(fields):
        raise ValueError("transforms must align 1:1 with fields")
    return list(transforms)


def iter_records(path: str) -> Iterator[Tuple[int, Dict[str, str]]]:
    """Yield (row_number, row_dict); row_number is 1-based with header = 1."""
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row_no, row in enumerate(reader, start=2):
            yield row_no, row


def make_key(
    row: Dict[str, str], fields: Sequence[str], transforms: Sequence[Transform]
) -> str:
    return SEP.join(transform(row[name]) for transform, name in zip(transforms, fields))


def count_classes(
    path: str,
    fields: Sequence[str],
    transforms: Optional[Sequence[Transform]] = None,
) -> Tuple[Counter, int]:
    """Pass 1: count equivalence-class sizes. Returns (counts, total_rows)."""
    transforms = _normalize_transforms(fields, transforms)
    counts: Counter = Counter()
    total = 0
    for _, row in iter_records(path):
        counts[make_key(row, fields, transforms)] += 1
        total += 1
    return counts, total


@dataclass
class RiskReport:
    """Quantified re-identification risk for one QI combination."""

    fields: List[str]
    transforms: List[str]
    k_threshold: int
    total_records: int
    num_classes: int
    min_class_size: int
    max_class_size: int
    mean_class_size: float
    median_class_size: float
    unique_classes: int          # classes of size 1
    unique_records: int          # records inside classes of size 1
    rare_classes: int            # classes of size < k_threshold
    rare_records: int            # records inside classes of size < k_threshold
    max_reident_probability: float  # 1 / min_class_size (worst-case prosecutor risk)
    size_histogram: Dict[str, int] = field(default_factory=dict)

    @property
    def is_k_anonymous(self) -> bool:
        return self.num_classes > 0 and self.min_class_size >= self.k_threshold

    def to_dict(self) -> dict:
        data = asdict(self)
        data["is_k_anonymous"] = self.is_k_anonymous
        return data


def build_report(
    counts: Counter,
    total: int,
    fields: Sequence[str],
    k: int = 5,
    transform_names: Optional[Sequence[str]] = None,
) -> RiskReport:
    sizes = sorted(counts.values())
    num_classes = len(sizes)
    if num_classes == 0:
        return RiskReport(
            fields=list(fields),
            transforms=list(transform_names or ["identity"] * len(fields)),
            k_threshold=k,
            total_records=total,
            num_classes=0,
            min_class_size=0,
            max_class_size=0,
            mean_class_size=0.0,
            median_class_size=0.0,
            unique_classes=0,
            unique_records=0,
            rare_classes=0,
            rare_records=0,
            max_reident_probability=0.0,
            size_histogram={label: 0 for label, _, _ in _HISTOGRAM_BUCKETS},
        )
    histogram = {}
    for label, lo, hi in _HISTOGRAM_BUCKETS:
        hi = hi if hi is not None else sizes[-1]
        histogram[label] = sum(1 for s in sizes if lo <= s <= hi)
    min_size = sizes[0]
    return RiskReport(
        fields=list(fields),
        transforms=list(transform_names or ["identity"] * len(fields)),
        k_threshold=k,
        total_records=total,
        num_classes=num_classes,
        min_class_size=min_size,
        max_class_size=sizes[-1],
        mean_class_size=round(total / num_classes, 4),
        median_class_size=statistics.median(sizes),
        unique_classes=sum(1 for s in sizes if s == 1),
        unique_records=sum(s for s in sizes if s == 1),
        rare_classes=sum(1 for s in sizes if s < k),
        rare_records=sum(s for s in sizes if s < k),
        max_reident_probability=round(1.0 / min_size, 6),
        size_histogram=histogram,
    )


def risky_class_map(counts: Counter, k: int) -> Dict[str, int]:
    """Map of class key -> size for every class smaller than k."""
    return {key: size for key, size in counts.items() if size < k}


def find_risky_records(
    path: str,
    fields: Sequence[str],
    risky: Dict[str, int],
    transforms: Optional[Sequence[Transform]] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, object]]:
    """Pass 2: locate concrete records inside risky classes (auditable list)."""
    transforms = _normalize_transforms(fields, transforms)
    found: List[Dict[str, object]] = []
    for row_no, row in iter_records(path):
        key = make_key(row, fields, transforms)
        if key in risky:
            entry: Dict[str, object] = {"row": row_no, "class_size": risky[key]}
            for name in fields:
                entry[name] = row[name]
            found.append(entry)
            if limit is not None and len(found) >= limit:
                break
    return found


def suppress_records(
    path: str,
    out_path: str,
    fields: Sequence[str],
    risky: Dict[str, int],
    transforms: Optional[Sequence[Transform]] = None,
    mode: str = "drop",
) -> Dict[str, int]:
    """Write an anonymized copy.

    mode="drop" removes records in risky classes; mode="mask" keeps them but
    replaces every QI value with "*". Returns suppression statistics.
    """
    if mode not in ("drop", "mask"):
        raise ValueError("mode must be 'drop' or 'mask'")
    transforms = _normalize_transforms(fields, transforms)
    stats = {"input_records": 0, "output_records": 0, "dropped": 0, "masked": 0}
    with open(path, newline="", encoding="utf-8") as fin, open(
        out_path, "w", newline="", encoding="utf-8"
    ) as fout:
        reader = csv.DictReader(fin)
        writer = csv.DictWriter(fout, fieldnames=reader.fieldnames)
        writer.writeheader()
        for row in reader:
            stats["input_records"] += 1
            key = make_key(row, fields, transforms)
            if key in risky:
                if mode == "drop":
                    stats["dropped"] += 1
                    continue
                for name in fields:
                    row[name] = "*"
                stats["masked"] += 1
            writer.writerow(row)
            stats["output_records"] += 1
    return stats


def screen_combinations(
    path: str,
    fields: Sequence[str], k: int = 5
) -> List[Dict[str, object]]:
    """Screen singles, pairs and the full set to justify the QI combination.

    One streaming pass per combination; intended for selection time, not for
    the production check on the full dataset.
    """
    combos: List[Tuple[str, ...]] = [(f,) for f in fields]
    combos += list(itertools.combinations(fields, 2))
    if len(fields) > 2:
        combos.append(tuple(fields))
    results = []
    for combo in combos:
        counts, total = count_classes(path, combo)
        report = build_report(counts, total, combo, k=k)
        results.append(
            {
                "combination": list(combo),
                "num_classes": report.num_classes,
                "min_class_size": report.min_class_size,
                "unique_classes": report.unique_classes,
                "rare_classes": report.rare_classes,
                "rare_records": report.rare_records,
                "max_reident_probability": report.max_reident_probability,
            }
        )
    return results
