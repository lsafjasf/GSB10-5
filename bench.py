"""性能基准：1000 个日志源、100000 次级别调整的耗时与内存峰值。

运行：python3 bench.py
"""

import resource
import sys
import time
import tracemalloc

sys.path.insert(0, ".")
from dynlog import Manager, ORDERED_LEVELS

N_SOURCES = 1000
N_ADJUSTS = 100_000


def main():
    tracemalloc.start()
    m = Manager(rate_limit=2000.0)
    for i in range(N_SOURCES):
        m.add_source("svc-%04d" % i, "INFO")
    # 一个本地节点（latency=0，立即生效）；远端推送场景见 simulate.py
    m.add_node("local", 0.0)

    t0 = time.perf_counter()
    for i in range(N_ADJUSTS):
        name = "svc-%04d" % (i % N_SOURCES)
        level = ORDERED_LEVELS[(i // N_SOURCES) % 5]
        m.adjust(name, level, actor="load-bot", reason="压测")
    elapsed = time.perf_counter() - t0

    cur, peak = tracemalloc.get_traced_memory()
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    tracemalloc.stop()

    print("== 动态级别调整性能基准 ==")
    print("日志源数量      : %d" % N_SOURCES)
    print("调整总次数      : %d" % N_ADJUSTS)
    print("审计记录条数    : %d" % len(m.audit))
    print("总耗时          : %.3f s" % elapsed)
    print("平均单次调整    : %.2f us" % (elapsed / N_ADJUSTS * 1e6))
    print("吞吐量          : %.0f 次调整/s" % (N_ADJUSTS / elapsed))
    print("Python 堆内存峰值: %.2f MB (tracemalloc)" % (peak / 1024 / 1024))
    print("进程 RSS 峰值    : %.1f MB (ru_maxrss)" % (rss_kb / 1024))
    print("每条审计记录均摊 : %.1f B" % (peak / N_ADJUSTS))


if __name__ == "__main__":
    main()
