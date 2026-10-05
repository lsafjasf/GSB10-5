"""性能基准: 10 万条文本的识别耗时与内存峰值。"""
import json
import os
import random
import resource
import time
import tracemalloc

import langid

BASE = os.path.dirname(os.path.abspath(__file__))
N = 100_000


def build_corpus():
    """按真实分布混合构造 10 万条文本 (长/短/混排/数字符号)。"""
    rng = random.Random(7)
    with open(os.path.join(BASE, "testset.jsonl"), encoding="utf-8") as f:
        items = [json.loads(ln) for ln in f]
    by_cat = {}
    for it in items:
        by_cat.setdefault(it["category"], []).append(it["text"])
    weights = [("long", 0.25), ("short", 0.45), ("ultra_short", 0.10),
               ("mixed", 0.15), ("digits_symbols", 0.05)]
    corpus = []
    for _ in range(N):
        r = rng.random()
        acc = 0.0
        for cat, w in weights:
            acc += w
            if r <= acc:
                corpus.append(rng.choice(by_cat[cat]))
                break
    return corpus


def main():
    det = langid.Detector()
    corpus = build_corpus()
    total_chars = sum(len(t) for t in corpus)
    print(f"corpus: {len(corpus)} texts, {total_chars} chars "
          f"(avg {total_chars / len(corpus):.1f} chars/text)")

    tracemalloc.start()
    t0 = time.perf_counter()
    answered = 0
    for text in corpus:
        if not det.detect(text)["refused"]:
            answered += 1
    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    print(f"total time:   {elapsed:.2f} s")
    print(f"per text:     {elapsed / len(corpus) * 1e6:.1f} us "
          f"({len(corpus) / elapsed:,.0f} texts/s)")
    print(f"answered:     {answered / len(corpus):.1%}, refused: "
          f"{1 - answered / len(corpus):.1%}")
    print(f"peak python heap (tracemalloc): {peak / 1024 / 1024:.1f} MiB")
    print(f"process max RSS (ru_maxrss):    {rss_kb / 1024:.1f} MiB")


if __name__ == "__main__":
    main()
