"""benchmark -- 10 万篇文档建库 / 查询压测。

文档为随机生成的中文文本（15~35 句，约 250~600 字）。
查询集 1000 条：500 条完全照抄、300 条拆散拼接(60字片段)、200 条完全无关。
内存峰值用 resource.ru_maxrss 度量（Linux，单位 KB）。
运行：python3 benchmark.py
"""

import random
import resource
import time

from leakfp import LeakDetector
from textgen import gen_doc, splice

N_DOCS = 100_000
N_EXACT, N_SPLICE, N_UNREL = 500, 300, 200


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def main() -> None:
    rng = random.Random(7)
    print(f"生成 {N_DOCS} 篇文档 ...")
    t0 = time.perf_counter()
    docs = [gen_doc(rng, rng.randint(15, 35)) for _ in range(N_DOCS)]
    avg_len = sum(len(d) for d in docs) / len(docs)
    print(f"  平均长度 {avg_len:.0f} 字，生成耗时 {time.perf_counter() - t0:.1f}s")

    det = LeakDetector()
    mem0 = rss_mb()
    t0 = time.perf_counter()
    det.add_many(docs)
    t1 = time.perf_counter()
    det.finalize()
    t2 = time.perf_counter()
    mem1 = rss_mb()
    st = det.stats()
    print(f"\n== 建库（{st['docs']} 篇）==")
    print(f"指纹总数        {st['fingerprints']}")
    print(f"索引体积        {st['index_bytes'] / 1e6:.1f} MB")
    print(f"指纹提取+入库   {t1 - t0:.1f}s  ({st['docs'] / (t1 - t0):.0f} 篇/s)")
    print(f"排序 finalize   {t2 - t1:.1f}s")
    print(f"建库总耗时      {t2 - t0:.1f}s")
    print(f"进程内存峰值    {mem1:.0f} MB（建库前 {mem0:.0f} MB）")

    queries = (
        [("照抄", docs[rng.randrange(N_DOCS)]) for _ in range(N_EXACT)]
        + [("拼接", splice(docs[rng.randrange(N_DOCS)], rng, 60))
           for _ in range(N_SPLICE)]
        + [("无关", gen_doc(rng, rng.randint(15, 35))) for _ in range(N_UNREL)]
    )
    t0 = time.perf_counter()
    hits = {"照抄": 0, "拼接": 0, "无关": 0}
    for kind, q in queries:
        if det.query(q):
            hits[kind] += 1
    dt = time.perf_counter() - t0
    print(f"\n== 查询（{len(queries)} 条）==")
    print(f"总耗时          {dt:.2f}s")
    print(f"平均延迟        {dt / len(queries) * 1000:.2f} ms/条")
    print(f"吞吐量          {len(queries) / dt:.0f} 条/s")
    print(f"照抄命中率      {hits['照抄']}/{N_EXACT}")
    print(f"拼接命中率      {hits['拼接']}/{N_SPLICE}")
    print(f"无关误报        {hits['无关']}/{N_UNREL}")
    print(f"查询后内存峰值  {rss_mb():.0f} MB")


if __name__ == "__main__":
    main()
