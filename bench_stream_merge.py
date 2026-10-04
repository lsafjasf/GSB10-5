"""100 万行输出的合并基准：python3 bench_stream_merge.py

测量：合并耗时、拆分耗时、Python 侧内存峰值（tracemalloc）、
进程级 RSS 高水位（ru_maxrss）、合并流大小与帧数。
"""

import hashlib
import os
import resource
import sys
import tempfile
import time
import tracemalloc

import stream_merge

LINES_PER_STREAM = 500_000  # 两流合计 100 万行


def line(tag: str, i: int) -> bytes:
    return f"{tag}-{i:08d}-" .encode() + b"z" * 30 + b"\n"


def main() -> int:
    child = (
        "import sys\n"
        f"n = {LINES_PER_STREAM}\n"
        "for i in range(n):\n"
        "    sys.stdout.write(f'o-{i:08d}-' + 'z' * 30 + '\\n')\n"
        "    sys.stderr.write(f'e-{i:08d}-' + 'z' * 30 + '\\n')\n"
        "    if i % 4096 == 0: sys.stdout.flush(); sys.stderr.flush()\n"
    )

    rss_start = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    tracemalloc.start()
    with tempfile.TemporaryFile() as merged_file, \
         tempfile.TemporaryFile() as out_file, \
         tempfile.TemporaryFile() as err_file:
        t0 = time.perf_counter()
        rc = stream_merge.run_merged(
            [sys.executable, "-u", "-c", child], merged_file)
        t1 = time.perf_counter()
        merged_size = merged_file.tell()

        merged_file.seek(0)
        stream_merge.split(merged_file, out_file, err_file)
        t2 = time.perf_counter()
        _, peak_py = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        rss_end = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

        out_size, err_size = out_file.tell(), err_file.tell()
        out_file.seek(0)
        err_file.seek(0)
        out_hash = hashlib.sha256()
        err_hash = hashlib.sha256()
        for block in iter(lambda: out_file.read(1 << 20), b""):
            out_hash.update(block)
        for block in iter(lambda: err_file.read(1 << 20), b""):
            err_hash.update(block)

    exp_out = hashlib.sha256()
    exp_err = hashlib.sha256()
    for i in range(LINES_PER_STREAM):
        exp_out.update(line("o", i))
        exp_err.update(line("e", i))
    assert rc == 0
    assert out_hash.hexdigest() == exp_out.hexdigest()
    assert err_hash.hexdigest() == exp_err.hexdigest()

    total_lines = LINES_PER_STREAM * 2
    total_bytes = exp_out.digest_size and (out_size + err_size)
    print("================ 100 万行合并基准 ================")
    print(f"总行数                 : {total_lines:,} (stdout {LINES_PER_STREAM:,} + stderr {LINES_PER_STREAM:,})")
    print(f"原始数据总量           : {out_size + err_size:,} 字节 "
          f"(stdout {out_size:,}, stderr {err_size:,})")
    print(f"合并流大小             : {merged_size:,} 字节 (帧头开销 "
          f"{merged_size - out_size - err_size:,} 字节, "
          f"{(merged_size - out_size - err_size) * 100 / merged_size:.2f}%)")
    print(f"合并耗时               : {t1 - t0:.3f} s  "
          f"({total_lines / (t1 - t0):,.0f} 行/s)")
    print(f"拆分耗时               : {t2 - t1:.3f} s  "
          f"({total_lines / (t2 - t1):,.0f} 行/s)")
    print(f"合计耗时               : {t2 - t0:.3f} s")
    print(f"Python 侧内存峰值      : {peak_py / 1024 / 1024:.2f} MB (tracemalloc)")
    print(f"进程 RSS 高水位(开始)  : {rss_start / 1024:.2f} MB")
    print(f"进程 RSS 高水位(结束)  : {rss_end / 1024:.2f} MB (ru_maxrss, 含解释器基线)")
    print(f"内容校验               : stdout/stderr SHA-256 全部一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
