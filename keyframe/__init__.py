"""纯标准库视频关键帧抽取库。

用法：
    from keyframe import KeyframeDetector
    det = KeyframeDetector(width, height)
    for gray in frames:          # gray: bytes, 长度 width*height
        det.feed(gray)
    events = det.finish()        # Event(kind, index, metrics, duration)
"""

from .detector import KeyframeDetector, Event
from .config import Config

__all__ = ["KeyframeDetector", "Event", "Config"]
