"""10 万文档建库 / 查询基准。

用法: python3 benchmark.py [文档数] [查询数]
"""
import resource
import sys
import random
import time

from leakfp import LeakIndex

N_DOCS = int(sys.argv[1]) if len(sys.argv) > 1 else 100_000
N_QUERIES = int(sys.argv[2]) if len(sys.argv) > 2 else 500
DOC_TOKENS = 120
SEED = 42


def rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def make_corpus(n, seed):
    rng = random.Random(seed)
    vocab = [f"w{i}" for i in range(3000)]
    docs = []
    for _ in range(n):
        docs.append(" ".join(rng.choice(vocab) for _ in range(DOC_TOKENS)))
    return docs


def main():
    print(f"生成语料: {N_DOCS} 篇 x {DOC_TOKENS} token ...")
    t0 = time.perf_counter()
    docs = make_corpus(N_DOCS, SEED)
    print(f"语料生成: {time.perf_counter() - t0:.1f}s, RSS {rss_mb():.0f} MB")

    idx = LeakIndex()
    t0 = time.perf_counter()
    for i, doc in enumerate(docs):
        idx.add(i, doc)
    build_s = time.perf_counter() - t0
    stats = idx.stats()
    print(f"\n[建库] {N_DOCS} 篇: {build_s:.1f}s "
          f"({build_s / N_DOCS * 1e3:.3f} ms/篇, {N_DOCS / build_s:.0f} 篇/s)")
    print(f"[索引] 不同指纹数: {stats['distinct_fingerprints']:,}")
    print(f"[内存] 峰值 RSS: {rss_mb():.0f} MB")

    rng = random.Random(SEED + 1)
    queries = [docs[rng.randrange(N_DOCS)] for _ in range(N_QUERIES // 2)]
    queries += [" ".join(f"q{i}_{j}" for j in range(DOC_TOKENS))
                for i in range(N_QUERIES - len(queries))]

    lat = []
    hits = 0
    for q in queries:
        t0 = time.perf_counter()
        if idx.query(q):
            hits += 1
        lat.append((time.perf_counter() - t0) * 1e3)
    lat.sort()
    print(f"\n[查询] {N_QUERIES} 次 (一半库内原文, 一半新文本), 命中 {hits} 次")
    print(f"  平均 {sum(lat)/len(lat):.2f} ms, "
          f"p50 {lat[len(lat)//2]:.2f} ms, p95 {lat[int(len(lat)*0.95)]:.2f} ms, "
          f"max {lat[-1]:.2f} ms")
    print(f"[内存] 最终峰值 RSS: {rss_mb():.0f} MB")


if __name__ == "__main__":
    main()
