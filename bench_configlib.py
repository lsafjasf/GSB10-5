"""10 万次配置加载基准。运行: python3 bench_configlib.py [次数]"""

import gc
import json
import os
import resource
import sys
import tempfile
import time
import tracemalloc

from configlib import Schema, item, load_config, required

SCHEMA = Schema([
    required("host"),
    item("port", "int", default=8080),
    item("debug", "bool", default=False),
    item("workers", "int", default=4),
    item("tags", "list", default=[]),
    item("log_level", "str", default="info",
         choices=["debug", "info", "warn", "error"]),
    item("timeout", "float", default=1.5),
    item("meta", "dict"),
])

ENV = {"APP_PORT": "9100", "APP_DEBUG": "true", "APP_TAGS": '["x", "y"]'}
ARGV = ["--workers", "8"]


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100_000

    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"host": "bench-host", "port": 9000, "log_level": "warn"}, fh)

    # 预热
    for _ in range(1000):
        load_config(SCHEMA, file_path=path, env=ENV, argv=ARGV)

    gc.collect()
    gc.disable()
    tracemalloc.start()
    t0 = time.perf_counter()
    for _ in range(n):
        load_config(SCHEMA, file_path=path, env=ENV, argv=ARGV)
    elapsed = time.perf_counter() - t0
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    gc.enable()
    gc.collect()
    os.unlink(path)

    maxrss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(f"加载次数        : {n:,}")
    print(f"总耗时          : {elapsed:.3f} s")
    print(f"单次平均耗时    : {elapsed / n * 1e6:.2f} us")
    print(f"吞吐            : {n / elapsed:,.0f} 次/s")
    print(f"tracemalloc 峰值: {peak / 1024 / 1024:.2f} MiB (当前 {current / 1024 / 1024:.2f} MiB)")
    print(f"进程 RSS 峰值   : {maxrss_kb / 1024:.2f} MiB (ru_maxrss)")
    print(f"Python          : {sys.version.split()[0]}  平台: {sys.platform}")


if __name__ == "__main__":
    main()
