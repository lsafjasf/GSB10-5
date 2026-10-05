#!/usr/bin/env python3
"""Benchmark: 1,000,000 (number, language) plural selections.

The number pool mixes integers, decimals, zero, negatives and huge
magnitudes; every selection goes through select_plural() with fraction
formatting + thousands grouping enabled, i.e. formatting and category
selection are both timed.
"""
import random
import time
from decimal import Decimal

from plural import select_plural

LANGS = ["en", "ru", "ar", "fr"]
N_NUMBERS = 250_000  # x 4 languages = 1,000,000 combinations


def build_pool(n: int, seed: int = 42):
    rng = random.Random(seed)
    pool = []
    for k in range(n):
        r = rng.random()
        if r < 0.30:      # small/medium integers (incl. 0 and 1)
            pool.append(rng.randint(0, 10_000))
        elif r < 0.45:    # decimals
            pool.append(round(rng.uniform(-5_000, 5_000), rng.randint(1, 4)))
        elif r < 0.60:    # negative integers
            pool.append(-rng.randint(0, 1_000_000))
        elif r < 0.70:    # huge integers
            pool.append(rng.randint(10**12, 10**30))
        elif r < 0.80:    # huge decimals
            pool.append(Decimal(rng.randint(10**18, 10**24)) / 100)
        elif r < 0.90:    # decimal strings with visible trailing zeros
            pool.append("%d.%0*d" % (rng.randint(0, 999),
                                     rng.randint(1, 3), rng.randint(0, 9)))
        else:             # boundary values
            pool.append([0, 1, 2, 3, 5, 11, 14, 21, 101, 111,
                         1.5, -1, -2, 1_000_000, 10**6 * 3][k % 15])
    return pool


def main():
    pool = build_pool(N_NUMBERS)
    print("pool: %d numbers x %d languages = %d combinations"
          % (len(pool), len(LANGS), len(pool) * len(LANGS)))

    # warmup
    for lang in LANGS:
        for v in pool[:2000]:
            select_plural(lang, v, min_frac=0, max_frac=2, grouping=True)

    per_lang = {}
    total = 0.0
    checksum = 0
    for lang in LANGS:
        start = time.perf_counter()
        for v in pool:
            r = select_plural(lang, v, min_frac=0, max_frac=2, grouping=True)
            checksum += len(r.category)
        per_lang[lang] = time.perf_counter() - start
        total += per_lang[lang]

    combos = len(pool) * len(LANGS)
    print("checksum: %d" % checksum)
    for lang in LANGS:
        print("  %s: %7.3f s  (%6.0f ns/op, %d ops)"
              % (lang, per_lang[lang], per_lang[lang] / len(pool) * 1e9,
                 len(pool)))
    print("TOTAL: %.3f s for %d combinations -> %.0f ns/op, %.0f ops/s"
          % (total, combos, total / combos * 1e9, combos / total))


if __name__ == "__main__":
    main()
