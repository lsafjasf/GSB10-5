"""1000 万条数据分窗基准（仅标准库；数据流式生成，不全量驻留）。

场景：模拟处理时间语义 —— 水位线随消费推进（每 10 条推进 1 秒），
事件时间在当前水位线附近抖动：
  90% 准时（水位线前 0~59s），8% 迟到但在宽限内（60~359s），
  2% 超迟到（>=360s，被丢弃并标记）。
时钟由本脚本注入，库内不读系统时间。

运行：python3 bench.py [N]
耗时用 perf_counter；内存峰值用 resource.ru_maxrss（进程 RSS 峰值，
Linux 下单位 KB，含解释器基线）。
"""

import gc
import random
import resource
import sys
import time

from window_align import Record, WindowAligner

N = int(sys.argv[1]) if len(sys.argv) > 1 else 10_000_000
WINDOW_SIZE = 60
ALLOWED_LATENESS = 300
RECORDS_PER_TICK = 10  # 每 10 条水位线推进 1 秒


def main():
    rng = random.Random(20261006)
    state = {"watermark": 1_000_000}
    aligner = WindowAligner(
        WINDOW_SIZE, clock=lambda: state["watermark"],
        allowed_lateness=ALLOWED_LATENESS)

    gc.collect()
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    t0 = time.perf_counter()

    n_on_time = n_late = n_dropped = 0
    for i in range(N):
        state["watermark"] = 1_000_000 + i // RECORDS_PER_TICK
        wm = state["watermark"]
        r = rng.random()
        if r < 0.90:
            delta = rng.randrange(-59, 1)      # 事件时间 >= 水位线，准时
        elif r < 0.98:
            delta = rng.randrange(1, 301)       # 迟到 1~300s，宽限内
        else:
            delta = rng.randrange(301, 100_000)  # 超迟到，丢弃标记
        a = aligner.ingest(Record(event_time=wm - delta, value=1.0))
        if a.status == "ON_TIME":
            n_on_time += 1
        elif a.status == "LATE_MERGED":
            n_late += 1
        else:
            n_dropped += 1

    wall = time.perf_counter() - t0
    rss_peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    results = aligner.results()
    kept = sum(r.count for r in results)
    assert kept + n_dropped == N, "守恒校验失败"

    print("数据量            : %d 条（流式生成并逐条 ingest，不全量驻留）" % N)
    print("窗口数            : %d 个（window_size=%ds）"
          % (len(results), WINDOW_SIZE))
    print("准时/迟到并入/丢弃: %d / %d / %d" % (n_on_time, n_late, n_dropped))
    print("分窗耗时          : %.3f s  (%.0f 条/秒)" % (wall, N / wall))
    print("进程 RSS 峰值     : %.2f MiB（ru_maxrss，含解释器基线 %.1f MiB）"
          % (rss_peak / 1024, rss_before / 1024))
    print("聚合净增 RSS      : 约 %.2f MiB" % ((rss_peak - rss_before) / 1024))
    print("守恒校验          : 在窗 %d + 丢弃 %d = %d 条，无遗漏"
          % (kept, n_dropped, kept + n_dropped))


if __name__ == "__main__":
    main()
