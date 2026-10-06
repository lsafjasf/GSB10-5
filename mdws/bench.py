"""10 万段落规范化基准：python3 -m mdws.bench [段落数]"""

import random
import resource
import sys
import time
import tracemalloc

from mdws import normalize as nz

WORDS = (
    "lorem ipsum dolor sit amet consectetur adipiscing elit sed do "
    "eiusmod tempor incididunt ut labore et dolore magna aliqua"
).split()


def gen_paragraph(rnd: random.Random) -> str:
    lines = []
    for _ in range(rnd.randint(2, 5)):
        words = [rnd.choice(WORDS) for _ in range(rnd.randint(6, 14))]
        sep = rnd.choice([" ", "  ", "   ", " \t ", "\t"])
        line = sep.join(words)
        if rnd.random() < 0.3:
            line += rnd.choice([" ", "   ", "\t "])
        if rnd.random() < 0.2:
            line = "  " + line
        lines.append(line)
    return "\n".join(lines)


def gen_document(n: int, seed: int = 42) -> str:
    rnd = random.Random(seed)
    return "\n\n".join(gen_paragraph(rnd) for _ in range(n)) + "\n"


def run(n: int = 100_000) -> dict:
    doc = gen_document(n)
    n_lines = doc.count("\n")
    n_bytes = len(doc.encode())

    tracemalloc.start()
    t0 = time.perf_counter()
    res = nz.normalize(doc)
    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "paragraphs": n,
        "lines": n_lines,
        "input_bytes": n_bytes,
        "output_bytes": len(res.text.encode()),
        "changes": len(res.changes),
        "seconds": elapsed,
        "paragraphs_per_sec": n / elapsed,
        "tracemalloc_peak_mb": peak / 1024 / 1024,
        "ru_maxrss_mb": rss_kb / 1024,
    }


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100_000
    stats = run(n)
    for key, val in stats.items():
        if isinstance(val, float):
            print(f"{key}: {val:,.2f}")
        else:
            print(f"{key}: {val:,}")


if __name__ == "__main__":
    main()
