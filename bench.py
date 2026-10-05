# -*- coding: utf-8 -*-
"""基准：5000 个标题的分页 + 目录生成耗时与内存峰值。

运行：python3 bench.py
"""
import resource
import time
import tracemalloc

from tocgen import Document, Heading, Paragraph, Layout, paginate, build_toc, render_toc

N = 5000


def build_doc(n: int) -> Document:
    body = []
    for i in range(1, n + 1):
        level = 1 if i % 10 == 1 else (2 if i % 3 else 3)
        body.append(Heading(level, f"第{i}个标题 性能测试用标题文本"))
        if i % 4 == 0:
            body.append(Paragraph("正文段落内容 " * 8))
    return Document(body=body)


def main() -> None:
    doc = build_doc(N)
    layout = Layout(lines_per_page=40, page_width=60)

    tracemalloc.start()
    t0 = time.perf_counter()
    p = paginate(doc, layout)
    t1 = time.perf_counter()
    entries = build_toc(doc, p, include_front=False)
    toc_text = render_toc(entries, width=60)
    t2 = time.perf_counter()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(f"标题数:            {len(entries)}")
    print(f"物理页数:          {len(p.pages)}")
    print(f"分页耗时:          {(t1 - t0) * 1000:.1f} ms")
    print(f"目录生成+渲染耗时: {(t2 - t1) * 1000:.1f} ms")
    print(f"总耗时:            {(t2 - t0) * 1000:.1f} ms")
    print(f"内存峰值(tracemalloc): {peak / 1024 / 1024:.2f} MiB")
    print(f"进程峰值 RSS:          {rss_kb / 1024:.2f} MiB")
    print(f"目录行数:          {len(toc_text.splitlines())}")


if __name__ == "__main__":
    main()
