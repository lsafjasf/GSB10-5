"""基准：100 万次 取密钥 + 验签，输出耗时与内存峰值。"""

import resource
import time
import tracemalloc

from keyring import KeyRing

T0 = 1_700_000_000
N = 1_000_000
DATA = b"payload-to-be-signed"


def main():
    ring = KeyRing(now_fn=lambda: T0 + 90)  # 冻结在轮换重叠期
    ring.add_key("k1", b"secret-old", not_before=T0, not_after=T0 + 100)
    ring.add_key("k2", b"secret-new", not_before=T0 + 80, not_after=T0 + 180)
    kid, sig = ring.sign(DATA)
    assert kid == "k2"

    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    tracemalloc.start()
    t0 = time.perf_counter()

    ok = 0
    for _ in range(N):
        key = ring.signing_key()          # 取密钥
        if ring.verify(key.kid, DATA, sig):  # 验签
            ok += 1

    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    assert ok == N, ok
    print("iterations      : %d (signing_key + verify)" % N)
    print("elapsed         : %.3f s" % elapsed)
    print("throughput      : %.0f ops/s" % (N / elapsed))
    print("avg per op      : %.2f us" % (elapsed / N * 1e6))
    print("tracemalloc peak: %.1f KiB" % (peak / 1024))
    print("maxrss before   : %.1f MiB" % (rss_before / 1024))
    print("maxrss after    : %.1f MiB" % (rss_after / 1024))


if __name__ == "__main__":
    main()
