"""解析轨迹（trace）数据结构与渲染。

轨迹记录的是解析过程中的确定性事件序列，每一步都可回放。
时间戳是相对容器本次操作起点的单调时钟毫秒数。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Dict, List, Optional


@dataclass
class Event:
    seq: int
    time_ms: float
    kind: str            # 事件类型，见 KIND_SYMBOL
    name: str
    depth: int           # 构造调用栈深度，便于缩进展示
    detail: str = ""
    data: Dict[str, Any] = field(default_factory=dict)


KIND_SYMBOL = {
    "resolve_begin": "RESOLVE-BEGIN",
    "visit": "VISIT",               # 进入一个组件
    "cached": "CACHED",             # 已构造，直接复用
    "dep_wait": "DEP-WAIT",         # 等待某个即时依赖先被构造
    "lazy_inject": "LAZY-INJECT",   # 注入延迟句柄（此时不取值）
    "construct_begin": "BEGIN",     # 工厂开始执行
    "construct_end": "END",         # 工厂执行结束
    "lazy_access": "LAZY-ACCESS",   # 延迟句柄第一次被真正取值
    "dispose": "DISPOSE",           # 回收一个已构造实例
    "fail": "FAIL",                 # 构造失败
    "cycle": "CYCLE",               # 检测到环
    "resolve_end": "RESOLVE-END",
}


class Trace:
    def __init__(self) -> None:
        self.events: List[Event] = []
        self._t0 = perf_counter()

    def reset_clock(self) -> None:
        self.events = []
        self._t0 = perf_counter()

    def emit(self, kind: str, name: str, depth: int, detail: str = "",
             **data: Any) -> Event:
        ev = Event(
            seq=len(self.events) + 1,
            time_ms=(perf_counter() - self._t0) * 1000.0,
            kind=kind,
            name=name,
            depth=depth,
            detail=detail,
            data=data,
        )
        self.events.append(ev)
        return ev

    MAX_INDENT = 12  # 视觉缩进上限；更深的层级用数字标注，避免深链时文本爆炸

    def render(self) -> str:
        lines = []
        for e in self.events:
            tag = KIND_SYMBOL.get(e.kind, e.kind)
            if e.depth <= self.MAX_INDENT:
                indent = "  " * e.depth
            else:
                indent = "  " * self.MAX_INDENT + f"[d={e.depth}] "
            extra = f"  {e.detail}" if e.detail else ""
            lines.append(f"#{e.seq:03d} {e.time_ms:10.3f} ms  {indent}{tag:14s} {e.name}{extra}")
        return "\n".join(lines)
