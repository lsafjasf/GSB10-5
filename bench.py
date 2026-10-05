"""性能基准：100 万次取密钥 + 100 万次验签的耗时与内存峰值。

用法：python3 bench.py [次数]   （默认 1_000_000）

口径说明：
- 时间：time.perf_counter 墙钟时间，单线程，含 Python 解释器开销。
- 内存：tracemalloc 统计循环期间 Python 堆的分配峰值（不含解释器基线）；
  另打印 resource.ru_maxrss 作为进程级峰值参考。
- 验签循环复用同一枚预先签好的令牌（真实服务里令牌来自请求，各不相同；
  此处为隔离"验签"本身的成本，令牌生成不计入循环）。
"""

import resource
import sys
import time
import tracemalloc

from keystore import FakeClock, KeyStore
from scenario import ALL_KEYS, K1, K2, PAYLOAD, make_signature


def deep_size(obj, _seen=None):
    """粗略递归统计对象内存占用（字节）。"""
    seen = _seen or set()
    if id(obj) in seen:
        return 0
    seen.add(id(obj))
    size = sys.getsizeof(obj)
    if isinstance(obj, dict):
        size += sum(deep_size(k, seen) + deep_size(v, seen) for k, v in obj.items())
    elif isinstance(obj, (list, tuple, set, frozenset)):
        size += sum(deep_size(i, seen) for i in obj)
    elif hasattr(obj, "__dict__"):
        size += deep_size(vars(obj), seen)
    elif hasattr(obj, "__slots__"):
        size += sum(
            deep_size(getattr(obj, s), seen)
            for s in obj.__slots__
            if hasattr(obj, s)
        )
    return size


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000
    store = KeyStore(ALL_KEYS, now=FakeClock(2500))
    token_k1 = make_signature(K1)  # 旧密钥签的令牌，轮换期间照常验
    token_k2 = make_signature(K2)

    print(f"Python {sys.version.split()[0]}  次数={n:,}")
    print(f"密钥库常驻内存（3 把密钥，deep_size 估算）: {deep_size(store.snapshot()):,} 字节")

    tracemalloc.start()

    t0 = time.perf_counter()
    hits = 0
    for i in range(n):
        if store.get_key("key-v2") is not None:
            hits += 1
    t_get = time.perf_counter() - t0
    assert hits == n

    t0 = time.perf_counter()
    ok = 0
    for i in range(n):
        if store.verify(PAYLOAD, token_k1 if i % 2 == 0 else token_k2):
            ok += 1
    t_verify = time.perf_counter() - t0
    assert ok == n

    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    total_ops = 2 * n
    t_total = t_get + t_verify
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    print(f"\n=== 基准结果 ===")
    print(f"取密钥 get_key   : {n:>12,} 次  {t_get:8.3f} s  {n / t_get / 1e6:6.2f} M ops/s")
    print(f"验签   verify    : {n:>12,} 次  {t_verify:8.3f} s  {n / t_verify / 1e6:6.2f} M ops/s")
    print(f"合计             : {total_ops:>12,} 次  {t_total:8.3f} s  {total_ops / t_total / 1e6:6.2f} M ops/s")
    print(f"循环期间 Python 堆分配峰值（tracemalloc）: {peak / 1024:,.1f} KiB")
    print(f"进程峰值 RSS（ru_maxrss，含解释器基线）  : {rss_kb / 1024:,.1f} MiB")


if __name__ == "__main__":
    main()
