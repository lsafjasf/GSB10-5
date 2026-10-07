"""Generate a deterministic demo dataset that exercises every risk scenario.

Columns:
  record_id, age, gender, zipcode, occupation, disease

Scenarios injected on top of a common-value base population:
  A unique combination      : a few near-unique individuals (rare age+zip+job)
  B large equivalence class : one identical block of 800 records
  C rare values             : occupations that occur only 1-3 times
  D combination sensitivity : single fields are frequent, the 4-tuple is unique
"""

from __future__ import annotations

import csv
import os
import random

SEED = 20261007
N_BASE = 12000

COMMON_ZIPS = ["3100%02d" % i for i in range(20, 60)]
COMMON_OCCUPATIONS = [
    "teacher", "nurse", "engineer", "driver", "clerk", "manager",
    "accountant", "sales", "doctor", "programmer", "farmer", "professor",
]
GENDERS = ["M", "F"]
DISEASES = ["flu", "hypertension", "diabetes", "asthma", "migraine", "gout"]


def generate(path):
    rnd = random.Random(SEED)
    rows = []

    for _ in range(N_BASE):
        rows.append([
            rnd.randint(18, 70),
            rnd.choice(GENDERS),
            rnd.choice(COMMON_ZIPS),
            rnd.choice(COMMON_OCCUPATIONS),
        ])

    # B: one large equivalence class of 800 identical QI tuples.
    for _ in range(800):
        rows.append([35, "F", "310025", "nurse"])

    # A: unique individuals (rare age + rare zip + rare occupation).
    rows.append([97, "F", "999001", "astronaut"])
    rows.append([99, "M", "999002", "astronaut"])
    rows.append([98, "F", "999003", "matador"])

    # C: rare values in otherwise-common context (occupation occurs 1-3 times).
    rows.append([44, "M", "310030", "astronaut"])
    rows.append([52, "F", "310041", "matador"])

    # D: 20 rows where every marginal value is common but the full tuple is
    # unique (each (age, zip) pair is used exactly once, all in common ranges).
    for i in range(20):
        rows.append([40 + (i // 2), GENDERS[i % 2],
                     COMMON_ZIPS[i % len(COMMON_ZIPS)], "teacher"])

    rnd.shuffle(rows)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["record_id", "age", "gender", "zipcode",
                         "occupation", "disease"])
        for idx, (age, gender, zipcode, occupation) in enumerate(rows, start=1):
            writer.writerow(["P%06d" % idx, age, gender, zipcode, occupation,
                             rnd.choice(DISEASES)])
    print("wrote %d rows to %s" % (len(rows), path))


if __name__ == "__main__":
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    generate(os.path.join(here, "data", "demo.csv"))
