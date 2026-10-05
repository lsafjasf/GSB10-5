from __future__ import annotations

import argparse
import gc
import hashlib
import json
from pathlib import Path
import platform
import sys
import time
import tracemalloc

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wordfreq import PROFILES, count_tokens  # noqa: E402


CORPUS_BLOCKS = (
    "这是一个纯中文段落，用来覆盖连续汉字和中文标点。",
    "This is an English paragraph with Hello, HELLO, and well-known words. ",
    "中英混排段落：我爱NLP，自然语言处理with AI。",
    "标点密集！！！……（测试）【边界】——done? abc_def 3.14 2026\n",
)


def build_corpus(target_chars: int) -> str:
    chunks: list[str] = []
    total = 0
    index = 0
    while total < target_chars:
        block = CORPUS_BLOCKS[index % len(CORPUS_BLOCKS)]
        chunks.append(block)
        total += len(block)
        index += 1
    return "".join(chunks)[:target_chars]


def run_benchmark(profile_name: str, target_chars: int) -> dict[str, object]:
    text = build_corpus(target_chars)
    assert len(text) == target_chars

    gc.collect()
    tracemalloc.start()
    started = time.perf_counter()
    counts = count_tokens(text, PROFILES[profile_name])
    elapsed = time.perf_counter() - started
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    return {
        "profile": profile_name,
        "input_characters": len(text),
        "input_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "total_tokens": sum(counts.values()),
        "unique_tokens": len(counts),
        "elapsed_seconds": round(elapsed, 6),
        "tracemalloc_peak_bytes": peak_bytes,
        "tracemalloc_peak_mib": round(peak_bytes / 1024 / 1024, 3),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark deterministic word counting")
    parser.add_argument("--profile", choices=sorted(PROFILES), default="unicode-v1")
    parser.add_argument("--chars", type=int, default=1_000_000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = run_benchmark(args.profile, args.chars)
    output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
