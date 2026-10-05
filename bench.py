"""Benchmark: normalize + equivalence over 100,000 paths.

Run: python3 bench.py
"""

import os
import random
import resource
import string
import sys
import time
import tracemalloc

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
import pathnorm as pn  # noqa: E402

N = 100_000


def make_corpus(n: int, seed: int = 42) -> list[str]:
    rng = random.Random(seed)
    words = ["src", "app", "Config", "DATA", "logs", "用户", "proj-01",
             "node_modules", "build", "Test.Case"]
    seps = ["/", "\\", "//", "/\\"]
    corpus = []
    for _ in range(n):
        depth = rng.randint(1, 8)
        parts = [rng.choice(words) + rng.choice(["", ".txt", ".yaml", ""])
                 for _ in range(depth)]
        path = rng.choice(seps).join(parts)
        style = rng.random()
        if style < 0.4:
            path = rng.choice(["C:", "D:", "c:"]) + "\\" + path
        elif style < 0.5:
            path = "/" + path
        if rng.random() < 0.2:
            path += rng.choice(["/./x", "/../y", "/."])
        corpus.append(path)
    return corpus


def peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def main() -> None:
    corpus = make_corpus(N)
    print(f"corpus: {N} paths, python {sys.version.split()[0]}, "
          f"host {sys.platform}")

    for style, cs in [("windows", False), ("windows", True),
                      ("posix", True), ("posix", False)]:
        tracemalloc.start()
        t0 = time.perf_counter()
        results = []
        for p in corpus:
            results.append(pn.normalize(p, style, case_sensitive=cs))
        dt = time.perf_counter() - t0
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        print(f"normalize style={style:7s} case_sensitive={cs!s:5s} "
              f"{dt*1000:9.1f} ms  ({dt/N*1e6:6.2f} us/path)  "
              f"tracemalloc peak {peak/1024/1024:6.2f} MiB  "
              f"maxRSS {peak_rss_mb():6.1f} MiB")
        del results

    # Equivalence: compare shuffled duplicate corpus against itself.
    twin = [p.replace("/", "\\") if "\\" not in p else p.replace("\\", "/")
            for p in corpus]
    t0 = time.perf_counter()
    eq = sum(pn.equivalent(a, b, "windows", case_sensitive=False)
             for a, b in zip(corpus, twin))
    dt = time.perf_counter() - t0
    print(f"equivalent x{N} pairs (windows, ci): {dt*1000:.1f} ms, "
          f"{eq} pairs equal "
          f"(100% expected: separator swap is a no-op)")


if __name__ == "__main__":
    main()
