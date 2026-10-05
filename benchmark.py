"""Benchmark citation renumbering with the Python standard library only."""

from __future__ import annotations

import argparse
import gc
import json
import random
import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from citation_renumber import renumber_document  # noqa: E402


def build_document(citation_points: int, entry_count: int, seed: int) -> str:
    rng = random.Random(seed)
    if citation_points < entry_count:
        raise ValueError("citation_points must be at least entry_count")

    numbers = list(range(1, entry_count + 1))
    rng.shuffle(numbers)
    numbers.extend(
        rng.randrange(1, entry_count + 1)
        for _ in range(citation_points - entry_count)
    )

    body = "".join(f"[{number}]" for number in numbers)
    references = "\n".join(
        f"[{number}] entry-{number:06d}" for number in range(1, entry_count + 1)
    )
    return body + "\n\n## 参考文献\n\n" + references + "\n"


def run_benchmark(citation_points: int, entry_count: int, seed: int) -> dict:
    document = build_document(citation_points, entry_count, seed)
    gc.collect()

    tracemalloc.start()
    start = time.perf_counter()
    result = renumber_document(document)
    elapsed_seconds = time.perf_counter() - start
    current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    try:
        import resource

        rss_peak_kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except Exception:  # pragma: no cover - Windows compatibility is optional
        rss_peak_kib = None

    consistency = result.consistency
    if not (
        consistency["body_content_byte_comparison"]["equal"]
        and consistency["citation_sequence"]["equal"]
        and consistency["reference_list"]["numbers_are_contiguous"]
        and consistency["reference_list"]["all_body_citations_resolve"]
    ):
        raise RuntimeError("benchmark document failed consistency checks")

    return {
        "citation_points": citation_points,
        "bibliography_entries": entry_count,
        "seed": seed,
        "elapsed_seconds": elapsed_seconds,
        "tracemalloc_current_bytes": current_bytes,
        "tracemalloc_peak_bytes": peak_bytes,
        "process_rss_peak_kib": rss_peak_kib,
        "input_chars": len(document),
        "output_chars": len(result.text),
        "memory_measurement_note": (
            "tracemalloc_peak_bytes 为重排调用期间的 Python 分配峰值；"
            "process_rss_peak_kib 为整个基准进程的 RSS 高水位"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--points", type=int, default=100_000)
    parser.add_argument("--entries", type=int, default=1_000)
    parser.add_argument("--seed", type=int, default=20261005)
    args = parser.parse_args()

    print(
        json.dumps(
            run_benchmark(args.points, args.entries, args.seed),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
