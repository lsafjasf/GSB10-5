"""10 万文件事件基准：处理耗时 + 内存峰值（tracemalloc / ru_maxrss）。

场景：20_000 个不同路径 x 5 个原始事件 = 100_000 事件，
每目录 5 个文件（低于折叠阈值 8），每 1_000 个事件 tick 一次。
"""

import resource
import time
import tracemalloc

from debouncer import Debouncer

N_PATHS = 20_000
EVENTS_PER_PATH = 5
N_EVENTS = N_PATHS * EVENTS_PER_PATH
FILES_PER_DIR = 5


def main() -> None:
    paths = [
        f"/w/dir{i // FILES_PER_DIR:05d}/f{i:06d}.txt" for i in range(N_PATHS)
    ]

    d = Debouncer(window=0.05, max_wait=0.5, dir_threshold=8)

    tracemalloc.start()
    t0 = time.perf_counter()

    fired = 0
    n = 0
    for round_no in range(EVENTS_PER_PATH):
        now = 1000.0 + round_no * 0.001
        for path in paths:
            d.feed(path, "modify", ts=now)
            n += 1
            if n % 1_000 == 0:
                fired += len(d.tick(now))
    fired += len(d.tick(1001.0))  # 收尾，静默窗口到期

    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    max_rss_mib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

    print(f"原始事件数        : {n:,}")
    print(f"触发逻辑事件数    : {fired:,}")
    print(f"剩余未决          : {d.pending_count():,}")
    print(f"总耗时(含事件构造): {elapsed*1000:.1f} ms")
    print(f"单事件均耗        : {elapsed/n*1e6:.2f} us")
    print(f"吞吐              : {n/elapsed:,.0f} events/s")
    print(f"内存峰值( traced ): {peak/1024/1024:.2f} MiB")
    print(f"进程峰值 RSS      : {max_rss_mib:.2f} MiB")

    assert fired == N_PATHS, (fired, N_PATHS)
    assert d.pending_count() == 0
    print("校验通过：触发数 == 路径数，无残留未决")


if __name__ == "__main__":
    main()
