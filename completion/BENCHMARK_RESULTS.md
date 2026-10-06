# Benchmark Results

```
Python 3.12.3 on linux | candidates=1,000,000 (public=980,000, secret=20,000) | queries=100,000 | limit=50
RSS at start: 13.8 MB

## 1. Index build
- loaded 1,000,000 names in 1.58 s (0.63 M names/s)
- groups: public=980,000, ops=98,000, secret=20,000
- RSS after build: 519.9 MB (delta +506.1 MB); process RSS peak (maxrss): 559.5 MB
- object accounting: strings=62.1 MB, sorted-list slots=8.8 MB, dict overhead=30.8 MB, total=101.7 MB
- gc.freeze() applied after build (index objects excluded from GC scans)

## 2. Completion latency — 100,000 queries as unprivileged 'dev'
- cold (result cache empty):
  n=100,000  mean=      2.4us  p50=     0.4  p90=     7.9  p95=    12.4  p99=     15.2  p99.9=      39.3  max=    1753.0us
- results: 4,234,013 names returned, 8,549/100,000 queries empty (8.5%); avg 42.34 names/query
- **unauthorized secret names leaked: 0** (checked 4,234,013 returned names against a 20,000-name secret set; includes 10,000 probes aimed at secret prefixes)
- warm (identical prefixes, cache hits):
  n=100,000  mean=      2.3us  p50=     0.3  p90=     7.7  p95=    12.3  p99=     15.1  p99.9=      42.5  max=    1856.3us
- warm-path leaks: 0

- broad prefixes (len<=3), n=40,159: n= 40,159  mean=      0.3us  p50=     0.2  p90=     0.3  p95=     0.4  p99=      0.7  p99.9=       3.1  max=      38.6us  leaks=0
- selective prefixes (len>=4), n=59,841: n= 59,841  mean=      3.6us  p50=     0.5  p90=    11.8  p95=    12.8  p99=     18.4  p99.9=      59.5  max=    1875.4us  leaks=0

## 3. Unauthenticated user 'guest' (zero groups)
- n=100,000  mean=      0.3us  p50=     0.2  p90=     0.5  p95=     0.5  p99=      0.7  p99.9=       3.2  max=     565.8us
- 0 names returned across 100,000 queries (100000 empty); leaks=0

## 4. Permission change during a session (deterministic outcome)
- 'secret rows' below = names from the secret group visible to 'admin';
  expected >0 while authorized, exactly 0 after revoke.
- before revoke (v5): 50000 secret rows returned (authorized)
  n=  1,000  mean=      0.3us  p50=     0.2  p90=     0.2  p95=     0.2  p99=      0.3  p99.9=       1.6  max=      48.7us
- after revoke (v6): 0 secret rows returned (1000/1,000 queries empty)  -> zero post-revoke exposure
  n=  1,000  mean=      0.2us  p50=     0.2  p90=     0.2  p95=     0.2  p99=      0.2  p99.9=       0.8  max=      12.3us
- after re-grant (v7): 50000 secret rows returned (authorized again)
  n=  1,000  mean=      0.3us  p50=     0.2  p90=     0.2  p95=     0.2  p99=      0.3  p99.9=       1.5  max=      13.0us
- permission revoked mid-iteration (streaming API): PermissionChanged raised: permissions changed v7->v8; re-issue the query
  rows yielded before change were all snapshot-consistent; re-query after revoke returned 0 names

## 5. Peak memory
- process RSS peak (maxrss) after build+benchmark: 559.5 MB
- RSS now: 529.3 MB; index object accounting: 101.7 MB for 1,000,000 candidates

```

Run: `python3 bench.py` (regenerates this data on your machine).
