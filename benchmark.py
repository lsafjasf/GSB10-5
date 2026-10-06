#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""refrenum 基准：10 万个引用点的重排耗时与内存峰值（仅标准库）。

运行: python3 benchmark.py [引用点数，默认 100000]
"""

import random
import resource
import sys
import time
import tracemalloc

import refrenum


def gen_doc(n_marks, n_entries=2000, seed=42):
    """生成含 n_marks 个引用点、n_entries 个条目的测试文档。"""
    rng = random.Random(seed)
    lines = []
    for i in range(n_marks):
        kind = rng.random()
        if kind < 0.6:
            mark = "[%d]" % rng.randint(1, n_entries)
        elif kind < 0.85:
            k = rng.randint(2, 4)
            mark = "[" + ",".join(str(rng.randint(1, n_entries)) for _ in range(k)) + "]"
        else:
            a = rng.randint(1, n_entries - 5)
            mark = "[%d-%d]" % (a, a + rng.randint(1, 5))
        lines.append("第 %d 段：已有研究 %s 表明……" % (i, mark))
    lines.append("")
    lines.append("## 参考文献")
    lines.append("")
    for n in range(1, n_entries + 1):
        lines.append("[%d] 条目 %d. 某期刊, 20%02d." % (n, n, n % 100))
    return "\n".join(lines).encode("utf-8")


def main():
    n_marks = int(sys.argv[1]) if len(sys.argv) > 1 else 100000
    doc = gen_doc(n_marks)
    print("引用点数: %d，文档大小: %.2f MB" % (n_marks, len(doc) / 1e6))

    # 第一次：纯耗时（无内存追踪开销）
    t0 = time.perf_counter()
    new_doc, report = refrenum.process(doc)
    pure = time.perf_counter() - t0

    # 第二次：tracemalloc 统计 Python 堆内存峰值
    tracemalloc.start()
    t0 = time.perf_counter()
    new_doc, report = refrenum.process(doc)
    elapsed = time.perf_counter() - t0
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0

    print("重排+校验耗时(无追踪): %.3f s" % pure)
    print("重排+校验耗时(tracemalloc 开启): %.3f s" % elapsed)
    print("内存峰值(tracemalloc Python 堆): %.2f MB" % (peak / 1e6))
    print("内存峰值(进程 RSS): %.2f MB" % rss_mb)
    print("被引条目: %d，改写标记: %d，校验通过: %s" % (
        report["counts"]["entries_cited"],
        report["counts"]["citation_marks_rewritten"],
        report["verification"]["ok"],
    ))
    assert report["verification"]["ok"]
    assert report["byte_compare"]["body_identical_outside_markers"]
    print("一致性校验: 通过（标记窗口之外正文逐字节相同）")


if __name__ == "__main__":
    main()
