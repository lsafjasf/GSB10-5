"""Generate a deterministic 1,000,000-row CSV for the performance benchmark."""

from __future__ import annotations

import csv
import os
import random

SEED = 7
N = 1_000_000


def generate(path, n=N):
    rnd = random.Random(SEED)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["record_id", "age", "gender", "zipcode",
                         "occupation", "city"])
        for i in range(1, n + 1):
            writer.writerow([
                i,
                rnd.randint(18, 90),
                rnd.choice(["M", "F"]),
                "%06d" % rnd.randint(100000, 104999),
                "job%02d" % rnd.randint(0, 49),
                "city%03d" % rnd.randint(0, 99),
            ])
    print("wrote %d rows to %s" % (n, path))


if __name__ == "__main__":
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    generate(os.path.join(here, "data", "benchmark_1m.csv"))
