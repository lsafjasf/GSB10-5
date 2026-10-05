"""10 万行对齐基准：耗时 + 内存峰值。"""

import random
import resource
import time
import tracemalloc

from numalign import align_table, display_width

N = 100_000
random.seed(20261005)

FW = "０１２３４５６７８９"


def make_cell(i):
    kind = i % 5
    if kind == 0:  # 纯半角
        return f"{random.uniform(-1e6, 1e6):.3f}"
    if kind == 1:  # 全角数字 + 全角负号
        v = f"{random.uniform(0, 1e5):.2f}"
        return "".join(FW[int(c)] if c.isdigit() else "．" for c in v)
    if kind == 2:  # 组合序列（数字上加重音符）
        v = f"{random.uniform(0, 1e4):.1f}"
        return v[:2] + "\u0301" + v[2:]
    if kind == 3:  # 极宽字符装饰
        return "\U0001F389" + f"{random.uniform(0, 1e3):.4f}"
    # 千分位 + 科学计数混合
    return f"{random.uniform(0, 1e7):,.1f}" if i % 2 else f"{random.uniform(0,1):.2e}"


t0 = time.perf_counter()
rows = [[make_cell(i * 3 + c) for c in range(3)] for i in range(N)]
t_gen = time.perf_counter() - t0
rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

tracemalloc.start()
t0 = time.perf_counter()
lines, starts = align_table(rows, gap=2, decimals=2, thousands=True)
t_align = time.perf_counter() - t0
_, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()
rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

assert len(lines) == N and len(starts) == N
ncols = 3
for c in range(ncols):
    col = {starts[r][c] for r in range(0, N, 997)}
    assert len(col) == 1, f"列 {c} 起始位置不一致: {col}"
print(f"行数                : {N:,} 行 x {ncols} 列")
print(f"数据生成耗时        : {t_gen:.3f} s")
print(f"对齐耗时            : {t_align:.3f} s  ({N / t_align:,.0f} 行/s)")
print(f"tracemalloc 峰值    : {peak / 1024 / 1024:.1f} MiB (对齐阶段 Python 对象)")
print(f"进程 RSS 峰值       : {rss_after:.1f} MiB (生成前基线 {rss_before:.1f} MiB)")
print(f"列起始位置(抽样一致): {[starts[0][c] for c in range(ncols)]}")
print("样例行:")
for r in (0, N // 2, N - 1):
    print(f"  |{lines[r]}|  width={display_width(lines[r])}")
