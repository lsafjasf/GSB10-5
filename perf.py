"""perf.py — 10 万条文件事件的处理耗时与内存峰值。

运行：python3 perf.py
场景一：100 个目录 x 1000 个文件，每文件 1 条事件，共 100,000 条（10s 事件流）；
场景二：单目录 10 万条事件（极端批量，跨 10s，会触发 max_wait 强制刷新）。
"""

import gc
import time
import tracemalloc

from debounce import CREATE, MODIFY, Debouncer, Event


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def bench(name, n_dirs, n_files, window=0.5, max_wait=5.0):
    clock = FakeClock()
    dbc = Debouncer(window=window, max_wait=max_wait, clock=clock)
    total = n_dirs * n_files
    triggers = []  # 记录每个触发的路径数

    gc.collect()
    tracemalloc.start()
    t0 = time.perf_counter()

    seq = 0
    for d in range(n_dirs):
        for f in range(n_files):
            seq += 1
            clock.t += 0.0001  # 模拟事件流时间推进（100k 条共 10s）
            dbc.feed(Event(path=f"/data/dir{d:04d}/f{f:05d}.bin",
                           kind=CREATE if f % 2 else MODIFY, seq=seq))
    feed_elapsed = time.perf_counter() - t0

    t1 = time.perf_counter()
    while dbc.pending_dirs:  # 推进到全部静默
        clock.t += 0.05
        triggers.extend(dbc.tick())
    flush_elapsed = time.perf_counter() - t1
    elapsed = feed_elapsed + flush_elapsed
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    covered = sum(len(t.paths) for t in triggers)
    resyncs = sum(1 for t in triggers if t.resync)
    print(f"[{name}]")
    print(f"  原始事件数      : {total:,}")
    print(f"  触发次数        : {len(triggers)}")
    print(f"  覆盖路径数      : {covered:,}（== 事件数即无漏触发）")
    print(f"  resync 次数     : {resyncs}（无 seq 空洞，应为 0）")
    print(f"  仅喂入耗时      : {feed_elapsed*1000:.1f} ms（{feed_elapsed/total*1e6:.2f} us/事件）")
    print(f"  flush 阶段耗时  : {flush_elapsed*1000:.1f} ms")
    print(f"  总耗时          : {elapsed*1000:.1f} ms")
    print(f"  内存峰值(增量)  : {peak/1024/1024:.2f} MiB")
    print(f"  flush 后待处理  : {dbc.pending_paths} 条")
    assert covered == total, "存在漏触发！"
    print()


if __name__ == "__main__":
    bench("100 目录 x 1000 文件", n_dirs=100, n_files=1000)
    bench("单目录 100,000 文件（极端批量）", n_dirs=1, n_files=100_000)
