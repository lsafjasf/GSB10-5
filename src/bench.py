"""基准：10 万次配置加载的耗时与内存峰值。运行: python3 bench.py [次数]"""

import gc
import resource
import sys
import time
import tracemalloc

from configkit import Field, load

SCHEMA = {
    "db_host": Field(str, required=True),
    "db_port": Field(int, default=5432),
    "debug": Field(bool, default=False),
    "log_level": Field(str, default="info"),
    "ratio": Field(float, default=0.5),
}
FILE_DATA = {"db_host": "file-db", "db_port": 5433, "extra_key": 1}
ENV = {"APP_DB_HOST": "env-db", "APP_DEBUG": "true", "APP_DBUG": "x"}
CLI = {"db_port": "6000"}


def main(n: int) -> None:
    gc.collect()
    tracemalloc.start()
    t0 = time.perf_counter()
    ok = 0
    for _ in range(n):
        r = load(SCHEMA, env=ENV, file_data=FILE_DATA, cli_data=CLI)
        ok += 1 if r.values["db_port"] == 6000 else 0
    elapsed = time.perf_counter() - t0
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    print(f"加载次数:        {n:,}")
    print(f"结果校验通过:    {ok:,}")
    print(f"总耗时:          {elapsed:.3f} s")
    print(f"单次平均:        {elapsed / n * 1e6:.2f} µs")
    print(f"吞吐量:          {n / elapsed:,.0f} 次/s")
    print(f"tracemalloc 峰值: {peak / 1024:.1f} KiB（当前 {current / 1024:.1f} KiB）")
    print(f"进程 RSS 峰值:   {rss_kb / 1024:.1f} MiB")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 100_000)
