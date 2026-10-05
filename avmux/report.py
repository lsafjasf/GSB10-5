"""轨迹报告：缺轨、时长不一致检测（只报告，不静默补齐），以及时间交叉计算。

报告项 ``Issue`` 的 severity：
* ``error``   —— 结构问题（缺轨、重复轨、空轨）；strict 模式下 mux/demux 抛错；
* ``warning`` —— 时长不一致；不会被自动补齐，调用方必须显式决定如何处理；
* ``info``    —— 仅说明（例如交叉区间比某条轨迹短）。

交叉区间（公共微秒时间基，前闭后开）：
    overlap_start_us = max(各轨 start_us)
    overlap_end_us   = min(各轨 end_us)
start/end 先以精确分数求出，再按微秒取整：
start 用 ROUND_NEAREST（与“时间点换算”口径一致），
end   用 ROUND_DOWN（保证 [start, end) 一定落在所有轨迹内部，不会因为取整
       凭空造出半微秒“重叠”）。
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import List, Optional

from .model import AUDIO, VIDEO, MediaFile, Track
from .timebase import (
    MICROSECOND_TIME_BASE,
    ROUND_DOWN,
    ROUND_NEAREST,
    TimeBase,
    convert_exact,
)

ERROR = "error"
WARNING = "warning"
INFO = "info"


@dataclass
class Issue:
    severity: str
    code: str
    message: str


@dataclass
class TrackSpan:
    """单条轨迹在公共微秒时间基下的时间区间（前闭后开）。"""

    track_type: str
    time_base: TimeBase
    start_ticks: int
    end_ticks: int
    start_us_exact: Fraction
    end_us_exact: Fraction
    start_us: int
    end_us: int

    @property
    def duration_us(self) -> int:
        return self.end_us - self.start_us


@dataclass
class Analysis:
    issues: List[Issue]
    spans: List[TrackSpan]
    overlap_start_us: Optional[int] = None
    overlap_end_us: Optional[int] = None
    overlap_start_us_exact: Optional[Fraction] = None
    overlap_end_us_exact: Optional[Fraction] = None

    @property
    def ok(self) -> bool:
        return not any(i.severity == ERROR for i in self.issues)

    @property
    def has_overlap(self) -> bool:
        return (
            self.overlap_start_us is not None
            and self.overlap_end_us is not None
            and self.overlap_start_us < self.overlap_end_us
        )

    @property
    def overlap_duration_us(self) -> Optional[int]:
        if not self.has_overlap:
            return None
        return self.overlap_end_us - self.overlap_start_us


def _span(track: Track) -> TrackSpan:
    start_ticks = track.start_ticks
    end_ticks = track.end_ticks
    start_exact = convert_exact(start_ticks, track.time_base, MICROSECOND_TIME_BASE)
    end_exact = convert_exact(end_ticks, track.time_base, MICROSECOND_TIME_BASE)
    start_us = track.time_base.ticks_to_us(start_ticks, ROUND_NEAREST)
    end_us = track.time_base.ticks_to_us(end_ticks, ROUND_DOWN)
    return TrackSpan(
        track_type=track.track_type,
        time_base=track.time_base,
        start_ticks=start_ticks,
        end_ticks=end_ticks,
        start_us_exact=start_exact,
        end_us_exact=end_exact,
        start_us=start_us,
        end_us=end_us,
    )


def analyze(media: MediaFile, require=(AUDIO, VIDEO)) -> Analysis:
    """分析轨迹：缺轨/重复轨/空轨 -> error；时长不一致 -> warning；计算交叉区间。"""
    issues: List[Issue] = []
    spans: List[TrackSpan] = []

    for required in require:
        found = [t for t in media.tracks if t.track_type == required]
        if not found:
            issues.append(
                Issue(ERROR, "missing_track", f"missing required track: {required}")
            )
        elif len(found) > 1:
            issues.append(
                Issue(ERROR, "duplicate_track", f"duplicate track type: {required}")
            )

    for track in media.tracks:
        if not track.samples:
            issues.append(
                Issue(ERROR, "empty_track", f"track {track.track_type} has no samples")
            )
            continue
        spans.append(_span(track))

    # 时长不一致：逐对比较公共时间基下的时长（只报告，不补齐）
    if len(spans) >= 2:
        base = spans[0]
        for other in spans[1:]:
            if other.duration_us != base.duration_us:
                diff = other.duration_us - base.duration_us
                issues.append(
                    Issue(
                        WARNING,
                        "duration_mismatch",
                        f"track duration mismatch: {base.track_type}={base.duration_us}us "
                        f"vs {other.track_type}={other.duration_us}us (diff={diff:+d}us); "
                        f"not auto-padded",
                    )
                )

    overlap_start_us = overlap_end_us = None
    overlap_start_exact = overlap_end_exact = None
    if spans:
        overlap_start_exact = max(s.start_us_exact for s in spans)
        overlap_end_exact = min(s.end_us_exact for s in spans)
        overlap_start_us = max(s.start_us for s in spans)
        overlap_end_us = min(s.end_us for s in spans)
        if overlap_start_us >= overlap_end_us:
            issues.append(
                Issue(
                    WARNING,
                    "no_overlap",
                    f"tracks do not overlap in time: "
                    f"[{overlap_start_us}, {overlap_end_us})us",
                )
            )
        else:
            for s in spans:
                if s.start_us > overlap_start_us or s.end_us < overlap_end_us:
                    issues.append(
                        Issue(
                            INFO,
                            "partial_overlap",
                            f"track {s.track_type} only partially covered by overlap "
                            f"[{overlap_start_us}, {overlap_end_us})us",
                        )
                    )

    return Analysis(
        issues=issues,
        spans=spans,
        overlap_start_us=overlap_start_us,
        overlap_end_us=overlap_end_us,
        overlap_start_us_exact=overlap_start_exact,
        overlap_end_us_exact=overlap_end_exact,
    )
