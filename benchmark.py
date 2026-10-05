#!/usr/bin/env python3
"""10 万条标注的核对耗时与内存峰值基准（仅标准库）。

运行：python3 benchmark.py
产物：outputs/benchmark.json、outputs/benchmark.txt
"""

import json
import os
import random
import resource
import sys
import time
import tracemalloc

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from annotation_consistency import Annotation, check_consistency
from annotation_consistency.metrics import all_pairwise_metrics

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
TOTAL_ANNOTATIONS = 100_000
DOC_LENGTH = 400
ANNOTATORS = ["alice", "bob", "carol"]
LABELS = ["PER", "ORG", "LOC", "DATE", "EVT"]


def generate_dataset(seed: int = 20261005):
    """生成确定性的 10 万条标注。

    每篇文档先生成互不相交的"参考"区间；各标注者基于参考区间做：
    原样复制(多数) / 边界微调(产生 span_overlap) / 换标签(label_conflict) /
    漏标 / 零长度点标注。重叠对保持稀疏，贴近真实返工场景。
    """
    rng = random.Random(seed)
    annotations = []

    doc_lengths = {}
    doc_idx = 0
    # 每文档约 18 个参考区间 × 3 名标注者 ≈ 52 条；生成到略超 10 万再裁剪零头
    while len(annotations) < TOTAL_ANNOTATIONS:
        doc_id = f"doc-{doc_idx:06d}"
        doc_lengths[doc_id] = DOC_LENGTH
        # 生成互不相交的参考区间（留出间距，避免连锁重叠）
        cursor = 0
        refs = []
        while cursor < DOC_LENGTH - 40:
            length = rng.randint(3, 12)
            gap = rng.randint(8, 18)
            refs.append((cursor, cursor + length))
            cursor += length + gap

        for who_idx, who in enumerate(ANNOTATORS):
            chosen = refs  # 每名标注者看到全部参考区间，差异来自下方噪声
            for start, end in chosen:
                roll = rng.random()
                if roll < 0.01:
                    # 零长度点标注（少数，位于区间起点）
                    annotations.append(Annotation(doc_id, start, start, "MARK", who))
                    continue
                if roll < 0.04:
                    # 漏标：该标注者不打此条
                    continue
                label = LABELS[((start + doc_idx) * 7) % len(LABELS)]
                if roll < 0.07:
                    # 换标签 -> 标签冲突
                    label = rng.choice([l for l in LABELS if l != label])
                if roll < 0.12:
                    # 边界微调（向两侧扩/缩 1~2 字符），标签不变
                    delta = rng.choice([-2, -1, 1, 2])
                    new_start = max(0, start + (delta if delta < 0 else 0))
                    new_end = end + (delta if delta > 0 else 0)
                    new_end = min(DOC_LENGTH, max(new_end, new_start + 1))
                    annotations.append(Annotation(doc_id, new_start, new_end, label, who))
                else:
                    annotations.append(Annotation(doc_id, start, end, label, who))
        doc_idx += 1

    # 统一裁剪零头，保证总数恰好 10 万（裁剪量 <2%，对一致率影响可忽略）
    rng.shuffle(annotations)
    if len(annotations) > TOTAL_ANNOTATIONS:
        annotations = annotations[:TOTAL_ANNOTATIONS]
    return annotations, doc_lengths


def run_once(annotations, doc_lengths):
    tracemalloc.start()
    t0 = time.perf_counter()
    result = check_consistency(annotations)
    t1 = time.perf_counter()
    metrics = all_pairwise_metrics(annotations, doc_lengths)
    t2 = time.perf_counter()
    _, py_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_peak_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "check_seconds": t1 - t0,
        "metrics_seconds": t2 - t1,
        "total_seconds": t2 - t0,
        "python_peak_bytes": py_peak,
        "process_peak_rss_bytes": rss_peak_kb * 1024,  # Linux ru_maxrss 单位为 KB
        "conflict_count": len(result.conflicts),
        "pair_record_count": len(result.pair_records),
        "mean_strict_f1": sum(m.exact_span_label.f1 for m in metrics) / len(metrics),
    }


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    annotations, doc_lengths = generate_dataset()
    assert len(annotations) == TOTAL_ANNOTATIONS, len(annotations)

    warmup = run_once(annotations, doc_lengths)  # 预热，不计入正式结果
    runs = [run_once(annotations, doc_lengths) for _ in range(3)]
    best = min(runs, key=lambda r: r["total_seconds"])

    report = {
        "annotation_count": TOTAL_ANNOTATIONS,
        "doc_count": len(doc_lengths),
        "annotators": ANNOTATORS,
        "conflict_count": best["conflict_count"],
        "pair_record_count": best["pair_record_count"],
        "timing_runs": [
            {
                "check_seconds": round(r["check_seconds"], 4),
                "metrics_seconds": round(r["metrics_seconds"], 4),
                "total_seconds": round(r["total_seconds"], 4),
            }
            for r in runs
        ],
        "best_total_seconds": round(best["total_seconds"], 4),
        "throughput_annotations_per_second": round(TOTAL_ANNOTATIONS / best["total_seconds"]),
        "python_peak_bytes": best["python_peak_bytes"],
        "process_peak_rss_bytes": best["process_peak_rss_bytes"],
        "mean_strict_pairwise_f1": round(best["mean_strict_f1"], 4),
    }

    with open(os.path.join(OUT_DIR, "benchmark.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    lines = [
        "10 万条标注一致性核对基准",
        f"标注总数      : {TOTAL_ANNOTATIONS:,}",
        f"文档数        : {len(doc_lengths):,}（每文档 {DOC_LENGTH} 字符）",
        f"标注者        : {', '.join(ANNOTATORS)}",
        f"冲突总数      : {best['conflict_count']:,}",
        f"成对记录总数  : {best['pair_record_count']:,}",
        f"平均严格口径 F1: {best['mean_strict_f1']:.4f}",
        "",
        "耗时（3 次运行，秒）：",
    ]
    for i, r in enumerate(runs, 1):
        lines.append(
            f"  run{i}: 核对 {r['check_seconds']:.4f}s"
            f" + 指标 {r['metrics_seconds']:.4f}s = {r['total_seconds']:.4f}s"
        )
    lines += [
        f"最佳总耗时    : {best['total_seconds']:.4f}s",
        f"吞吐          : {TOTAL_ANNOTATIONS / best['total_seconds']:,.0f} 条/秒",
        f"Python 分配峰值: {best['python_peak_bytes'] / 1024 / 1024:.2f} MiB"
        "（tracemalloc，仅核对阶段新增对象）",
        f"进程 RSS 峰值 : {best['process_peak_rss_bytes'] / 1024 / 1024:.2f} MiB"
        "（ru_maxrss，含输入数据与解释器）",
    ]
    text = "\n".join(lines)
    with open(os.path.join(OUT_DIR, "benchmark.txt"), "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    print(text)
    print(f"\n已写出: {os.path.join(OUT_DIR, 'benchmark.json')}")


if __name__ == "__main__":
    main()
