"""10 万条标注的核对耗时与内存峰值基准（确定性随机种子）。"""
import random
import resource
import sys
import time
import tracemalloc

from consistency import Annotation, check_consistency

N_ANNOTATIONS = 100_000
N_ITEMS = 10_000
ANNOTATORS = ["ann_a", "ann_b", "ann_c"]
LABELS = ["PER", "ORG", "LOC", "GPE", "MISC"]
TEXT_LEN = 200


def generate(seed=42):
    rng = random.Random(seed)
    anns = []
    for i in range(N_ANNOTATIONS):
        item = f"doc-{rng.randrange(N_ITEMS):05d}"
        who = ANNOTATORS[rng.randrange(len(ANNOTATORS))]
        start = rng.randrange(TEXT_LEN)
        # 约 1% 零长度区间
        if rng.random() < 0.01:
            end = start
        else:
            end = min(TEXT_LEN, start + 1 + rng.randrange(20))
        label = LABELS[rng.randrange(len(LABELS))]
        anns.append(Annotation(item, who, start, end, label))
    return anns


def main():
    print(f"生成 {N_ANNOTATIONS} 条标注（{N_ITEMS} 个条目，"
          f"{len(ANNOTATORS)} 名标注者）...")
    anns = generate()

    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    tracemalloc.start()
    t0 = time.perf_counter()

    report = check_consistency(anns)

    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    print(f"核对耗时:        {elapsed:.3f} s")
    print(f"Python 堆峰值:   {peak / 1024 / 1024:.1f} MiB (tracemalloc)")
    print(f"进程 RSS 峰值:   {rss_after / 1024:.1f} MiB (ru_maxrss, 本次增量 "
          f"{(rss_after - rss_before) / 1024:.1f} MiB)")
    print(f"冲突数:          {len(report.conflicts)}")
    print(f"零长度异常:      {len(report.zero_length)}")
    print(f"整体 strict:     {report.strict_overall:.4f}")
    print(f"整体 relaxed:    {report.relaxed_overall:.4f}")
    print(f"Python:          {sys.version.split()[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
