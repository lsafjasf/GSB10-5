"""1000 万条分窗压测：耗时 + 内存峰值。运行：python3 benchmark.py [n]"""

import resource
import sys
import time
import tracemalloc
from array import array

from windowing import WindowAssigner

N = int(sys.argv[1]) if len(sys.argv) > 1 else 10_000_000
SIZE = 60_000          # 1 分钟窗口
DAY = 86_400_000
WATERMARK = DAY        # 注入水位线：数据主体在其前后


def gen(n):
    # 确定性伪随机事件时间，均匀跨 2 天，另含 1% 迟到、1% 超迟到、0.1% 负时间戳（时钟偏移）
    state = 42
    out = array("q")
    append = out.append
    for _ in range(n):
        state = (state * 6364136223846793005 + 1442695040888963407) & (2**64 - 1)
        r = state % 1000
        if r < 1:
            append(-((state >> 10) % DAY))   # 时钟偏移：负时间戳
        elif r < 11:
            append(WATERMARK - 300_000)      # 超迟到
        elif r < 21:
            append(WATERMARK - 30_000)       # 迟到（落在允许迟到区间内）
        else:
            append(WATERMARK - 30_000 + (state >> 10) % DAY)  # 主体：水位线前后一天
    return out


def assign_all(data, assigner):
    counts = {}
    counts_get = counts.get
    late = dropped = 0
    size, lat, wm = assigner.size, assigner.allowed_lateness, WATERMARK
    for ts in data:
        start = (ts // size) * size          # window_of 内联（offset=0）
        end = start + size
        if end > wm:
            counts[start] = counts_get(start, 0) + 1
        elif end + lat > wm:
            counts[start] = counts_get(start, 0) + 1
            late += 1
        else:
            dropped += 1
    return counts, late, dropped


def main():
    assigner = WindowAssigner(size_ms=SIZE, allowed_lateness_ms=60_000)

    t0 = time.perf_counter()
    data = gen(N)
    t_gen = time.perf_counter() - t0

    # 计时运行（不带内存追踪，避免观测开销）
    t0 = time.perf_counter()
    counts, late, dropped = assign_all(data, assigner)
    t_assign = time.perf_counter() - t0

    # 内存运行（tracemalloc 观测 Python 堆峰值）
    tracemalloc.start()
    assign_all(data, assigner)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    maxrss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    total = sum(counts.values()) + dropped

    print(f"记录数:            {N:,}")
    print(f"窗口数:            {len(counts):,}")
    print(f"迟到并入:          {late:,}")
    print(f"超迟到丢弃:        {dropped:,}")
    print(f"总量守恒:          {total:,} == {N:,} -> {total == N}")
    print(f"数据生成耗时:      {t_gen:.2f} s")
    print(f"分窗+聚合耗时:     {t_assign:.2f} s  ({N / t_assign / 1e6:.2f} M 条/s)")
    print(f"Python 堆内存峰值: {peak / 2**20:.1f} MiB (tracemalloc, 聚合状态)")
    print(f"进程 RSS 峰值:     {maxrss_mb:.1f} MiB (ru_maxrss, 含 80MB 输入缓冲)")


if __name__ == "__main__":
    main()
