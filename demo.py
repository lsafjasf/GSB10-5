"""打印对拍数据的逐条判定结果与窗口聚合。

运行：python3 demo.py
"""

from window_align import (
    ON_TIME, LATE_MERGED, TOO_LATE_DROPPED,
    Record, batch_align,
)

WINDOW_SIZE = 60
ORIGIN = 1000
WATERMARK = 1060
ALLOWED_LATENESS = 60

# 固定对拍数据（同一份在测试里以 20 种乱序 + 逆序对拍）
FIXTURE = [
    Record(event_time=1000, value=1.0, key="A"),  # 恰好左边界
    Record(event_time=1059, value=2.0, key="B"),
    Record(event_time=1060, value=3.0, key="C"),  # 恰好右边界 -> 下一窗
    Record(event_time=1119, value=4.0, key="D"),
    Record(event_time=1120, value=5.0, key="E"),
    Record(event_time=1001, value=6.0, key="F"),  # 迟到 59，宽限内
    Record(event_time=900, value=9.0, key="G"),   # 超迟到 160，丢弃
]

LABEL = {
    ON_TIME: "准时",
    LATE_MERGED: "迟到-并入原窗口并重算",
    TOO_LATE_DROPPED: "超迟到-丢弃并标记",
}


def main():
    assignments, results = batch_align(
        FIXTURE, WINDOW_SIZE, WATERMARK, ALLOWED_LATENESS, origin=ORIGIN)

    print("参数: window_size=%d origin=%d watermark=%d allowed_lateness=%d"
          % (WINDOW_SIZE, ORIGIN, WATERMARK, ALLOWED_LATENESS))
    print("窗口规则: [start, end) 左闭右开\n")
    print("逐条判定（与到达顺序无关）:")
    print(" key  event  -> 归属窗口        状态                          lateness")
    for a in sorted(assignments, key=lambda x: x.record.key):
        win = ("[%d,%d)" % (a.window.start, a.window.end)
               if a.window else "   -- 丢弃 --   ")
        print("  %s  %5d      %-18s %-28s %d"
              % (a.record.key, a.record.event_time, win,
                 LABEL[a.status], a.lateness))

    print("\n窗口结果:")
    print("  窗口区间          count  sum   revised(被迟到数据重算)")
    for r in results:
        print("  [%5d,%5d)  %4d  %4.1f  %s"
              % (r.window.start, r.window.end, r.count, r.total, r.revised))

    n_drop = sum(1 for a in assignments if a.status == TOO_LATE_DROPPED)
    assert sum(r.count for r in results) + n_drop == len(FIXTURE)
    print("\n守恒校验: 在窗 %d 条 + 丢弃 %d 条 = 输入 %d 条，无落在缝里"
          % (sum(r.count for r in results), n_drop, len(FIXTURE)))


if __name__ == "__main__":
    main()
