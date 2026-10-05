"""基准：100 万次作用域校验的耗时与内存峰值。

运行: python3 bench.py [次数，默认 1000000]
"""

import resource
import sys
import time
import tracemalloc

from scope import covers

# 混合负载：精确匹配 / 上级作用域 / 通配 / 拒绝（前缀互含）/ 非法作用域
WORKLOAD = [
    ("files.read", "files.read"),
    ("files", "files.read.chunk"),
    ("files.*.read", "files.doc.read"),
    ("files.read", "files.readonly"),      # 越权，应拒
    ("files.read*", "files.readonly"),     # 非法授权，应拒
    ("*", "any.deep.scope.here"),
]


def run(total: int):
    n = len(WORKLOAD)
    allowed = 0
    start = time.perf_counter()
    for i in range(total):
        grant, request = WORKLOAD[i % n]
        if covers(grant, request):
            allowed += 1
    elapsed = time.perf_counter() - start
    return elapsed, allowed


def main() -> None:
    total = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000
    n = len(WORKLOAD)
    full, rem = divmod(total, n)
    expected = full * 4 + sum(1 for grant, req in WORKLOAD[:rem] if covers(grant, req))

    # 1) 真实耗时：不挂 tracemalloc，避免其统计开销
    elapsed, allowed = run(total)
    self_consistency = "" if allowed == expected else "  <-- 与预期不一致!"

    # 2) 内存峰值：单独跑一遍，tracemalloc 抓 Python 堆瞬时峰值
    tracemalloc.start()
    run(total)
    _, py_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    max_rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    print("校验次数        : %d" % total)
    print("总耗时          : %.3f s" % elapsed)
    print("单次平均        : %.3f us" % (elapsed / total * 1e6))
    print("吞吐量          : %.0f 次/s" % (total / elapsed))
    print("放行次数        : %d（预期 %d）%s" % (allowed, expected, self_consistency))
    print("Python 堆峰值   : %.1f KiB（tracemalloc，100 万次循环中任一时刻的最大新增分配）"
          % (py_peak / 1024))
    print("进程 RSS 峰值   : %.1f MiB（ru_maxrss，含解释器与代码）" % (max_rss_kb / 1024))


if __name__ == "__main__":
    main()
