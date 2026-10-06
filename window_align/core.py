"""批处理窗口对齐库（仅标准库）。

归属规则（务必先读）：
- 时间轴被切分为固定长度 window_size 的半开区间窗口：
    窗口 k 覆盖 [origin + k*window_size, origin + (k+1)*window_size)
- 左闭右开：恰好落在窗口左边界 -> 属于该窗口；
  恰好落在窗口右边界 -> 属于【下一个】窗口。
- 任何事件时间 t（含负数）都有唯一归属窗口，不存在"缝"。
  归属公式：k = floor((t - origin) / window_size)，Python 地板除对负数同样成立。

迟到处理（基于注入时钟的水位线）：
- watermark = clock.now()（由测试/调用方注入，库内不读系统时间）。
- lateness = watermark - event_time（基于修正后的事件时间，见时钟偏移）。
- lateness <= 0                -> ON_TIME，正常并入窗口。
- 0 < lateness <= allowed_lateness -> LATE_MERGED：并入原窗口，该窗口
  结果被重算并标记 revised=True（调用方应替换旧结果）。
- lateness > allowed_lateness  -> TOO_LATE_DROPPED：不进任何窗口，
  进入 dropped 列表并带原因，保证每条数据都有明确去向。

时钟偏移：
- 数据源时钟与处理时钟的偏差用 clock_offset 修正：
  corrected_event_time = raw_event_time + clock_offset。
  归属与迟到判定一律使用修正后的事件时间。

顺序无关性：
- 聚合（count/sum）满足交换律与结合律；batch_align 对全部记录使用
  同一个水位线快照，因此结果与到达顺序完全无关（测试中有乱序对拍）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Tuple

ON_TIME = "ON_TIME"
LATE_MERGED = "LATE_MERGED"
TOO_LATE_DROPPED = "TOO_LATE_DROPPED"


@dataclass(frozen=True, order=True)
class Window:
    """半开区间 [start, end)。"""

    index: int
    start: int
    end: int

    def contains(self, t: int) -> bool:
        return self.start <= t < self.end


@dataclass(frozen=True)
class Record:
    """输入数据。event_time 为源时钟原始事件时间（未修正）。"""

    event_time: int
    value: float = 1.0
    key: str = ""


@dataclass(frozen=True)
class Assignment:
    """每条数据的判定结果：归属窗口 + 状态 + 迟到时长。"""

    record: Record
    corrected_event_time: int
    window: Optional[Window]  # TOO_LATE_DROPPED 时为 None
    status: str
    lateness: int


@dataclass
class WindowResult:
    window: Window
    count: int = 0
    total: float = 0.0
    revised: bool = False  # 是否因迟到数据被重算过

    def as_tuple(self) -> Tuple[int, int, int, float, bool]:
        return (self.window.index, self.window.start, self.window.end,
                self.count, self.total)


def window_of(t: int, window_size: int, origin: int = 0) -> Window:
    """t 归属的唯一窗口。边界规则：左闭右开，右边界归下一窗口。"""
    if window_size <= 0:
        raise ValueError("window_size must be positive")
    k = (t - origin) // window_size  # 地板除，负数时间也唯一归属
    start = origin + k * window_size
    return Window(index=k, start=start, end=start + window_size)


def classify(corrected_event_time: int, watermark: int,
             allowed_lateness: int) -> Tuple[str, int]:
    """返回 (status, lateness)。"""
    lateness = watermark - corrected_event_time
    if lateness <= 0:
        return ON_TIME, lateness
    if lateness <= allowed_lateness:
        return LATE_MERGED, lateness
    return TOO_LATE_DROPPED, lateness


def batch_align(records: Iterable[Record],
                window_size: int,
                watermark: int,
                allowed_lateness: int = 0,
                clock_offset: int = 0,
                origin: int = 0
                ) -> Tuple[List[Assignment], List[WindowResult]]:
    """批处理对齐：对全部记录使用同一水位线快照，结果与到达顺序无关。

    返回 (每条数据的判定结果列表, 按窗口起始时间排序的窗口结果列表)。
    """
    assignments: List[Assignment] = []
    windows: Dict[int, WindowResult] = {}
    had_late: Dict[int, bool] = {}

    for rec in records:
        t = rec.event_time + clock_offset
        status, lateness = classify(t, watermark, allowed_lateness)
        if status == TOO_LATE_DROPPED:
            assignments.append(Assignment(rec, t, None, status, lateness))
            continue
        win = window_of(t, window_size, origin)
        assignments.append(Assignment(rec, t, win, status, lateness))
        res = windows.get(win.index)
        if res is None:
            res = windows[win.index] = WindowResult(window=win)
        res.count += 1
        res.total += rec.value
        if status == LATE_MERGED:
            had_late[win.index] = True

    results = sorted(windows.values(), key=lambda r: r.window.start)
    for res in results:
        res.revised = had_late.get(res.window.index, False)
    return assignments, results


class WindowAligner:
    """流式对齐器。时钟由外部注入：clock 是返回当前水位线的可调用对象。"""

    def __init__(self,
                 window_size: int,
                 clock: Callable[[], int],
                 allowed_lateness: int = 0,
                 clock_offset: int = 0,
                 origin: int = 0):
        if window_size <= 0:
            raise ValueError("window_size must be positive")
        self.window_size = window_size
        self.clock = clock
        self.allowed_lateness = allowed_lateness
        self.clock_offset = clock_offset
        self.origin = origin
        self._windows: Dict[int, WindowResult] = {}
        self.dropped: List[Assignment] = []

    def ingest(self, rec: Record) -> Assignment:
        watermark = self.clock()
        t = rec.event_time + self.clock_offset
        status, lateness = classify(t, watermark, self.allowed_lateness)
        if status == TOO_LATE_DROPPED:
            a = Assignment(rec, t, None, status, lateness)
            self.dropped.append(a)
            return a
        win = window_of(t, self.window_size, self.origin)
        res = self._windows.get(win.index)
        if res is None:
            res = self._windows[win.index] = WindowResult(window=win)
        res.count += 1
        res.total += rec.value
        if status == LATE_MERGED:
            res.revised = True  # 窗口被迟到数据触发重算
        return Assignment(rec, t, win, status, lateness)

    def results(self) -> List[WindowResult]:
        """按窗口起始时间排序输出，保证与到达顺序无关。"""
        return sorted(self._windows.values(), key=lambda r: r.window.start)
