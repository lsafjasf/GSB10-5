"""100 万次会话操作基准：耗时 + 内存峰值。

操作混合（模拟真实负载）：
  - 10 万次 create（新会话）
  - 80 万次 touch（续期，注入的虚拟时钟每秒前进 1s）
  - 10 万次 remaining（查询两个剩余时间）
  - 每 1 万步 sweep 一次清理过期会话（不计入操作数）

用法：python3 benchmark.py
"""

import resource
import time

from session_expiry import SessionStore

IDLE = 10
ABSOLUTE = 30
TOTAL_OPS = 1_000_000


def main():
    store = SessionStore(idle_timeout=IDLE, absolute_timeout=ABSOLUTE)
    now = 0
    ops = 0
    sid_seq = 0

    start = time.perf_counter()
    while ops < TOTAL_OPS:
        # 每 10 次操作：1 create + 8 touch + 1 remaining
        store.create(f"s{sid_seq}", now)
        sid_seq += 1
        ops += 1
        for _ in range(8):
            store.touch(f"s{sid_seq - 1}", now)
            ops += 1
        store.remaining(f"s{sid_seq - 1}", now)
        ops += 1
        now += 1
        if sid_seq % 10_000 == 0:
            store.sweep(now)
    elapsed = time.perf_counter() - start

    store.sweep(now + ABSOLUTE + 1)
    peak_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    print(f"操作总数        : {ops:,}")
    print(f"总耗时          : {elapsed:.3f} s")
    print(f"吞吐            : {ops / elapsed:,.0f} ops/s")
    print(f"单次操作平均耗时: {elapsed / ops * 1e6:.3f} us")
    print(f"内存峰值 (RSS)  : {peak_kb / 1024:.1f} MiB")


if __name__ == "__main__":
    main()
