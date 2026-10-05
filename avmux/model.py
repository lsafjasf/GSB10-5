"""内存中的媒体数据模型。

* ``Sample``：一个编码样本（帧/包），pts/dts 以**所属轨迹的时间基**计。
* ``Track`` ：一条轨迹（audio/video），自带时间基与样本列表。
* ``MediaFile``：若干轨迹的集合，mux 的输入、demux 的输出。

轨迹时间区间约定：**前闭后开** [start, end)，
start = 最小 pts，end = 最大 pts + 该样本 duration（都在轨迹自己的时间基内）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .timebase import TimeBase

AUDIO = "audio"
VIDEO = "video"
TRACK_TYPES = (AUDIO, VIDEO)


@dataclass
class Sample:
    pts: int          # 显示时间戳（轨迹时间基 tick）
    dts: int          # 解码时间戳（轨迹时间基 tick）
    duration: int     # 时长（轨迹时间基 tick）
    data: bytes
    keyframe: bool = False


@dataclass
class Track:
    track_type: str
    time_base: TimeBase
    name: str = ""
    samples: List[Sample] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.track_type not in TRACK_TYPES:
            raise ValueError(f"unknown track type: {self.track_type!r}")

    # ---- 轨迹时间区间（轨迹自身时间基，前闭后开）----
    @property
    def start_ticks(self) -> int:
        if not self.samples:
            raise ValueError("track has no samples")
        return min(s.pts for s in self.samples)

    @property
    def end_ticks(self) -> int:
        if not self.samples:
            raise ValueError("track has no samples")
        return max(s.pts + s.duration for s in self.samples)

    @property
    def duration_ticks(self) -> int:
        return self.end_ticks - self.start_ticks


@dataclass
class MediaFile:
    tracks: List[Track] = field(default_factory=list)
    creation_time: int = 0  # 秒级时间戳，0 表示未设置

    def get_track(self, track_type: str) -> Optional[Track]:
        for t in self.tracks:
            if t.track_type == track_type:
                return t
        return None
