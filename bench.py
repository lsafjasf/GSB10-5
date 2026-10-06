# -*- coding: utf-8 -*-
"""基准：10 万次展开的耗时与内存峰值。

运行：python3 bench.py [次数，默认 100000]

测量口径：
- 每次展开都新建 EnvExpander（冷缓存，不含任何跨次复用）；
- 耗时：time.perf_counter 墙钟时间；
- 内存峰值 1：tracemalloc 统计的解释器堆分配峰值（测量区间内的增量峰值）；
- 内存峰值 2：resource.getrusage 的 ru_maxrss（进程级峰值 RSS，Linux 单位 KiB）。
"""

import resource
import sys
import time
import tracemalloc

from envexpand import EnvExpander

ENV = {
    "USER": "alice",
    "HOME": "/home/alice",
    "PROJECT": "demo",
    "DIR": "logs",
    "APP": "$HOME/$PROJECT",
    "LOGS": "$APP/$DIR",
    "CFG": "${APP}/conf",
    "NEST": "${USER}@${CFG}",
}
TEMPLATE = "u=$USER home=$HOME logs=$LOGS cfg=$CFG nest=$NEST literal=$$HOME end"
EXPECTED = (
    "u=alice home=/home/alice logs=/home/alice/demo/logs "
    "cfg=/home/alice/demo/conf nest=alice@/home/alice/demo/conf "
    "literal=$HOME end"
)


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100_000

    # 预热 + 正确性校验
    got = EnvExpander(ENV).expand(TEMPLATE)
    assert got == EXPECTED, "基准模板展开结果不符合预期: %r" % got

    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    tracemalloc.start()
    t0 = time.perf_counter()
    checksum = 0
    for _ in range(n):
        checksum += len(EnvExpander(ENV).expand(TEMPLATE))
    elapsed = time.perf_counter() - t0
    _, peak_traced = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    assert checksum == n * len(EXPECTED)

    print("平台:            %s / Python %s" % (sys.platform, sys.version.split()[0]))
    print("展开次数:        %d（每次新建 EnvExpander，冷缓存）" % n)
    print("模板:            %r" % TEMPLATE)
    print("总耗时:          %.3f s" % elapsed)
    print("单次平均:        %.2f µs" % (elapsed / n * 1e6))
    print("吞吐:            %.0f 次/秒" % (n / elapsed))
    print("堆存活峰值:      %.2f MiB（tracemalloc 高水位；每次结果即弃，约等于单次工作集）"
          % (peak_traced / 1024**2))
    print("进程峰值 RSS:    %.2f MiB -> %.2f MiB（ru_maxrss，增量 %.2f MiB）"
          % (rss_before / 1024, rss_after / 1024, (rss_after - rss_before) / 1024))

    # 对照场景：保留全部 10 万个结果（最坏内存场景）
    tracemalloc.start()
    t0 = time.perf_counter()
    kept = [EnvExpander(ENV).expand(TEMPLATE) for _ in range(n)]
    elapsed_keep = time.perf_counter() - t0
    _, peak_keep = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_keep = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    assert len(kept) == n
    print("\n对照场景（保留全部 %d 个结果到列表）:" % n)
    print("总耗时:          %.3f s" % elapsed_keep)
    print("堆存活峰值:      %.2f MiB（其中结果字符串净内容 %.2f MiB）"
          % (peak_keep / 1024**2, sum(map(len, kept)) / 1024**2))
    print("进程峰值 RSS:    %.2f MiB（ru_maxrss）" % (rss_keep / 1024))


if __name__ == "__main__":
    main()
