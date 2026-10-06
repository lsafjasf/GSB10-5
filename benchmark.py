"""基准：10 万条事件的分级耗时与内存峰值。

运行：python3 benchmark.py [N]   （默认 N=100000）
"""
import random
import sys
import time
import tracemalloc

from seclib import Asset, Event, SecurityEngine, Signal


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100_000
    rng = random.Random(42)
    clock_now = [1_000_000.0]
    engine = SecurityEngine(clock=lambda: clock_now[0])

    tracemalloc.start()
    t0 = time.perf_counter()
    for i in range(n):
        k = rng.randint(1, 4)
        event = Event(
            event_id=f"evt-{i}",
            asset=Asset(f"asset-{i % 500}", rng.randint(1, 5)),
            scope_level=rng.randint(1, 5),
            signals=[Signal(f"sig-{j}", rng.choices([1, 2, 3, 4, 5],
                                              weights=[40, 25, 15, 12, 8])[0])
                    for j in range(k)],
        )
        engine.ingest(event)
        clock_now[0] += rng.randint(1, 600)
        engine.acknowledge(event.event_id)
    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    stats = engine.stats()
    print(f"事件数量        : {n:,}")
    print(f"总耗时          : {elapsed:.3f} s")
    print(f"单条平均耗时    : {elapsed / n * 1e6:.1f} us")
    print(f"吞吐量          : {n / elapsed:,.0f} 条/s")
    print(f"内存峰值        : {peak / 1024 / 1024:.1f} MiB (tracemalloc)")
    print(f"级别分布        : {stats['level_counts']}")
    print(f"超时事件数      : {stats['overdue']} (超时率 {stats['overdue_rate']:.2%})")
    print(f"平均响应耗时    : {stats['avg_response_seconds']:.1f} s")
    print(f"最大响应耗时    : {stats['max_response_seconds']:.1f} s")


if __name__ == "__main__":
    main()
