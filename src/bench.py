"""bench.py —— 5000 个标题的目录生成耗时与内存峰值基准。

运行：python3 bench.py
"""
import os
import resource
import sys
import time
import tracemalloc

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from toc_lib import (  # noqa: E402
    Heading, LayoutConfig, Paragraph, Section, build, verify,
)

CHAPTERS = 500          # 500 章
SECTIONS_PER_CHAPTER = 9  # 每章 9 个二级标题
HEADINGS = CHAPTERS * (1 + SECTIONS_PER_CHAPTER)  # 5000


def make_doc():
    cfg = LayoutConfig(page_height=60, page_width=72)
    blocks = []
    for c in range(1, CHAPTERS + 1):
        blocks.append(Heading(1, f"第{c}章 主题{c}"))
        blocks.append(Paragraph("正文段落。" * 20))
        for s in range(1, SECTIONS_PER_CHAPTER + 1):
            blocks.append(Heading(2, f"{c}.{s} 小节标题{c}x{s}"))
            blocks.append(Paragraph("这一小节的正文内容，用于撑开页面。" * 8))
    return cfg, [Section("front", [Paragraph("扉页")]),
                 Section("toc", []),
                 Section("body", blocks)]


def main():
    cfg, doc = make_doc()
    n_headings = sum(1 for sec in doc for b in sec.blocks
                     if isinstance(b, Heading))
    assert n_headings == HEADINGS, n_headings

    # 计时：tracemalloc 会拖慢分配密集代码，故分开测量，取 3 次最优
    best = float("inf")
    for _ in range(3):
        t0 = time.perf_counter()
        res = build(doc, cfg)
        best = min(best, time.perf_counter() - t0)

    tracemalloc.start()
    res = build(doc, cfg)
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    rows = verify(res)
    bad = sum(1 for r in rows if not r.ok)
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    print(f"标题数            : {n_headings}")
    print(f"正文物理页        : {res.total_pages}")
    print(f"目录页数          : {res.toc_page_count}")
    print(f"不动点迭代轮数    : {res.iterations}")
    print(f"逐项校验          : {len(rows) - bad}/{len(rows)} 一致")
    print(f"生成耗时(3次最优) : {best:.3f} s")
    print(f"内存峰值(tracemalloc): {peak / 1024 / 1024:.1f} MiB")
    print(f"进程峰值 RSS      : {rss_kb / 1024:.1f} MiB")


if __name__ == "__main__":
    main()
