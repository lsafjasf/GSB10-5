"""发布窗口冲突检测库（仅依赖 Python 标准库）。

核心语义：冲突基于"资源占用"而非单纯的"时间重叠"。
- 两个发布单时间重叠但不动同一资源 -> 不冲突；
- 两个发布单动同一资源但占用时段不相交 -> 不冲突；
- 一组发布单在同一资源上"同时占用需求之和 > 资源容量" -> 冲突，
  且按"最小冲突集"报告（任意真子集都不冲突的最小集合）。
"""

from .model import Conflict, Release, Resource, ScheduledRelease
from .detector import detect_conflicts
from .scheduler import schedule

__all__ = [
    "Conflict",
    "Release",
    "Resource",
    "ScheduledRelease",
    "detect_conflicts",
    "schedule",
]
