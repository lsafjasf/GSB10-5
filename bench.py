"""性能压测：1000 个日志源、10 万次级别调整。

指标：总耗时、吞吐（ops/s）、tracemalloc 内存峰值、进程 RSS 峰值，
并在结束后做一次全量一致性校验确认无未生效节点。
"""

import resource
import time
import tracemalloc

from dynlog import DEBUG, INFO, WARN, ERROR, LogLevelManager

SOURCE_COUNT = 1000
ADJUSTMENTS = 100_000
LEVELS = (DEBUG, INFO, WARN, ERROR)


def main():
    manager = LogLevelManager(delay_fn=lambda source: 0.0)
    for index in range(SOURCE_COUNT):
        manager.add_source("src-%04d" % index, INFO)

    tracemalloc.start()
    started = time.perf_counter()
    for i in range(ADJUSTMENTS):
        manager.set_level(
            "src-%04d" % (i % SOURCE_COUNT),
            LEVELS[i % len(LEVELS)],
            operator="bench",
            reason="perf-run",
        )
        if i % 5000 == 4999:
            manager.advance(0.001)
    manager.advance(1.0)
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    report = manager.consistency_check()
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    print("sources=%d adjustments=%d" % (SOURCE_COUNT, ADJUSTMENTS))
    print("wall_time=%.3fs  throughput=%.0f ops/s  avg=%.2fus/op"
          % (elapsed, ADJUSTMENTS / elapsed, elapsed / ADJUSTMENTS * 1e6))
    print("tracemalloc_peak=%.1fMB  ru_maxrss=%.1fMB"
          % (peak / 1e6, rss_kb / 1024.0))
    print("audit_records=%d" % len(manager.audit))
    print("final consistency: %s" % report.summary())


if __name__ == "__main__":
    main()
