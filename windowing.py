"""批处理事件时间窗口对齐库（仅标准库）。

归属规则（必须先看这里）：
- 窗口为半开区间 [start, end)，即 start <= event_time < end。
- 恰好落在窗口右边界 end 上的事件，归属【下一个】窗口（右侧）。
- 窗口起点对齐到 offset：start = offset + k * size，k 为整数（可为负）。
- 任意整数时间戳（含负数、含时钟偏移产生的异常值）都有且仅有一个归属窗口，
  不存在"落在缝里"的情况：floor 划分对全体整数是完备且互斥的。

迟到处理（批处理模式，水位线由测试/调用方注入）：
- watermark 表示"认为不会再有 event_time <= watermark 的正常数据"。
- window_end > watermark                 -> ON_TIME，正常计入窗口。
- window_end <= watermark < end+lateness -> LATE，仍并入原窗口（触发该窗口重算），
  记录被标记 late，可单独统计。
- watermark >= window_end + lateness     -> DROPPED（超迟到），不计入任何窗口，
  进入侧输出（side output）计数，保证总量可对账。

顺序无关性：
- 分类只依赖 (event_time, watermark)，与到达顺序无关。
- 批处理入口 assign_batch 默认取 watermark = 批次内最大 event_time，
  该值是数据的函数而非到达顺序的函数，因此乱序到达结果完全一致。
"""

from collections import namedtuple

ON_TIME = "on_time"
LATE = "late"
DROPPED = "dropped"

Assignment = namedtuple(
    "Assignment",
    ["event_time", "window_start", "window_end", "status"],
)


class WindowAssigner:
    """固定大小滚动窗口（tumbling window）分配器。"""

    def __init__(self, size_ms, offset_ms=0, allowed_lateness_ms=0):
        if size_ms <= 0:
            raise ValueError("size_ms must be positive")
        if allowed_lateness_ms < 0:
            raise ValueError("allowed_lateness_ms must be >= 0")
        self.size = size_ms
        self.offset = offset_ms
        self.allowed_lateness = allowed_lateness_ms

    def window_of(self, event_time):
        """返回事件归属的半开窗口 [start, end)。纯函数，与任何状态无关。"""
        k = (event_time - self.offset) // self.size  # 地板除，负数也正确
        start = self.offset + k * self.size
        return start, start + self.size

    def classify(self, event_time, watermark):
        """判定单条记录的归属窗口与迟到状态。"""
        start, end = self.window_of(event_time)
        if end > watermark:
            status = ON_TIME
        elif end + self.allowed_lateness > watermark:
            status = LATE
        else:
            status = DROPPED
        return Assignment(event_time, start, end, status)

    def assign_batch(self, event_times, watermark=None):
        """批量判定。watermark 缺省时 = max(event_times)（整批数据已就绪的批处理语义）。"""
        ts_list = event_times if isinstance(event_times, list) else list(event_times)
        if watermark is None:
            if not ts_list:
                return []
            watermark = max(ts_list)
        return [self.classify(ts, watermark) for ts in ts_list]


class WindowedCounter:
    """窗口聚合器：按窗口计数，迟到数据并入原窗口（重算），超迟到进侧输出。

    最终聚合结果只取决于数据多重集合与 watermark，与到达顺序无关。
    """

    def __init__(self, assigner):
        self.assigner = assigner
        self.counts = {}          # window_start -> count（含 on_time + late）
        self.late_counts = {}     # window_start -> 其中迟到数据的条数
        self.dropped = 0          # 侧输出：超迟到被丢弃的条数

    def add(self, event_time, watermark):
        a = self.assigner.classify(event_time, watermark)
        if a.status == DROPPED:
            self.dropped += 1
        else:
            self.counts[a.window_start] = self.counts.get(a.window_start, 0) + 1
            if a.status == LATE:
                self.late_counts[a.window_start] = self.late_counts.get(a.window_start, 0) + 1
        return a

    def add_batch(self, event_times, watermark=None):
        ts_list = event_times if isinstance(event_times, list) else list(event_times)
        if watermark is None:
            if not ts_list:
                return
            watermark = max(ts_list)
        for ts in ts_list:
            self.add(ts, watermark)

    def result(self):
        """返回确定性结果：按键排序，便于乱序对拍直接比较。"""
        return {
            "counts": dict(sorted(self.counts.items())),
            "late_counts": dict(sorted(self.late_counts.items())),
            "dropped": self.dropped,
        }
