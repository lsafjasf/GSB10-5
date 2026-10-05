"""1000 次启动-清理基准：报告总耗时、单次统计与内存峰值。

运行: python3 bench.py [次数]
"""
import os
import resource
import shutil
import statistics
import sys
import time
import tracemalloc

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from procguard import Supervisor  # noqa: E402


def main(n=1000):
    sleep_bin = shutil.which("sleep")
    argv = [sleep_bin, "30"] if sleep_bin else [
        sys.executable, "-c", "import time; time.sleep(30)"]

    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # KiB
    tracemalloc.start()
    durations = []
    escalations = 0
    leftovers = 0

    t0 = time.perf_counter()
    for _ in range(n):
        sup = Supervisor(argv, grace=0.5, poll_interval=0.002)
        sup.start()
        start = time.perf_counter()
        report = sup.terminate()
        durations.append(time.perf_counter() - start)
        sup.restore_handlers()
        escalations += int(report.escalated)
        leftovers += len(report.survivors)
    total = time.perf_counter() - t0

    py_current, py_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    print(f"次数               : {n}")
    print(f"目标进程           : {' '.join(argv)}")
    print(f"总耗时             : {total:.3f} 秒")
    print(f"单次清理 平均      : {statistics.mean(durations)*1e3:.3f} 毫秒")
    print(f"单次清理 中位      : {statistics.median(durations)*1e3:.3f} 毫秒")
    print(f"单次清理 最小/最大 : {min(durations)*1e3:.3f} / {max(durations)*1e3:.3f} 毫秒")
    print(f"触发强制兜底次数   : {escalations}")
    print(f"遗留进程总数       : {leftovers}")
    print(f"RSS 峰值(前/后)    : {rss_before} / {rss_after} KiB (ru_maxrss)")
    print(f"Python 堆内存峰值  : {py_peak/1024:.1f} KiB (tracemalloc)")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 1000)
