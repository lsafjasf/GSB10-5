"""100 万次登录会话操作基准：耗时 + 内存峰值。

操作模型：单会话反复“登录 -> 换号 -> 用新号再登录”，共 1,000,000 次
login() 调用（每次含：查表、删旧标识、CSPRNG 生成新标识、迁移、插入）。
同时给出 10 万并发活跃会话下的内存占用参考。

运行：python3 benchmark.py
"""

import resource
import time

from session_lib import SessionManager

N = 1_000_000

mgr = SessionManager()
sid = mgr.create_anonymous()

start = time.perf_counter()
for _ in range(N):
    sid = mgr.login(sid, user_id="bench_user", credential_ok=True)
elapsed = time.perf_counter() - start

peak_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # Linux: KiB
print(f"[基准] 登录换号操作 {N:,} 次")
print(f"  总耗时      : {elapsed:.3f} s")
print(f"  平均每次    : {elapsed / N * 1e6:.2f} µs")
print(f"  吞吐量      : {N / elapsed:,.0f} ops/s")
print(f"  进程峰值 RSS: {peak_kb / 1024:.1f} MiB (ru_maxrss)")
print(f"  活跃会话数  : {mgr.active_count} (换号不累积，旧标识全部即时回收)")

# 参考：大量并发活跃会话的内存占用
mgr2 = SessionManager()
t0 = time.perf_counter()
ids = [mgr2.create_anonymous() for _ in range(100_000)]
for i in ids:
    mgr2.login(i, user_id="u", credential_ok=True)
t1 = time.perf_counter()
peak2_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
print(f"[参考] 100,000 个并发活跃会话（各登录 1 次）")
print(f"  建会话+登录耗时: {t1 - t0:.3f} s")
print(f"  进程峰值 RSS   : {peak2_kb / 1024:.1f} MiB")
