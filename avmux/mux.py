"""封装（mux）：把 MediaFile 写成 AVMX1 文件。

* 先做轨迹分析（缺轨/时长不一致 -> 报告；strict=True 时 error 直接抛异常）；
* 包按公共微秒时间基下的 dts 交错排序（同刻按 track_id、再按原始 dts 稳定排序），
  排序键用 ROUND_NEAREST 换算；
* 样本 pts/dts/duration 以**轨迹自己的时间基**原样写入，不做换算；
* 流式写盘 + 增量 CRC32，内存占用与素材时长无关。
"""

from __future__ import annotations

import heapq
import zlib
from typing import BinaryIO, Optional, Union

from .format import (
    END_MAGIC,
    FLAG_KEYFRAME,
    HEADER_STRUCT,
    MAGIC,
    PACKET_HEADER_STRUCT,
    TRACK_FIXED_STRUCT,
    TRACK_TAIL_STRUCT,
    TRAILER_STRUCT,
    TYPE_TO_ID,
    VERSION,
)
from .model import MediaFile
from .report import Analysis, Issue, analyze
from .timebase import ROUND_NEAREST


class MuxError(Exception):
    pass


def _write(crc: list, fh: BinaryIO, data: bytes) -> None:
    fh.write(data)
    crc[0] = zlib.crc32(data, crc[0])


def mux(
    media: MediaFile,
    target: Union[str, BinaryIO],
    strict: bool = False,
    required=("audio", "video"),
) -> Analysis:
    """把 media 封装到 target（路径或二进制文件对象）。返回分析报告。

    required：期望必须存在的轨迹类型；缺失只报告（strict 时抛错），不补齐。
    单轨迹文件可传 required=("audio",) 或 ()。
    """
    analysis = analyze(media, require=required)
    if strict and not analysis.ok:
        msgs = "; ".join(i.message for i in analysis.issues if i.severity == "error")
        raise MuxError(f"refusing to mux (strict): {msgs}")

    track_ids = {id(track): idx for idx, track in enumerate(media.tracks)}

    # 交错键：公共微秒时间基下的 dts（ROUND_NEAREST），再按 track_id、样本序号。
    # 每条轨迹自身已按 dts 有序，用 heapq.merge 流式归并，内存 O(轨迹数)，与时长无关。
    def _packet_stream(track, tid):
        tb = track.time_base
        for index, sample in enumerate(track.samples):
            yield (tb.ticks_to_us(sample.dts, ROUND_NEAREST), tid, index, sample)

    streams = [_packet_stream(track, track_ids[id(track)]) for track in media.tracks]

    own_fh: Optional[BinaryIO] = None
    if isinstance(target, (str, bytes)):
        own_fh = open(target, "wb")
        fh = own_fh
    else:
        fh = target
    try:
        crc = [0]
        _write(crc, fh, HEADER_STRUCT.pack(MAGIC, VERSION, 0, 0, media.creation_time))
        _write(crc, fh, len(media.tracks).to_bytes(2, "little"))
        for track in media.tracks:
            name = track.name.encode("utf-8")
            _write(
                crc,
                fh,
                TRACK_FIXED_STRUCT.pack(
                    track_ids[id(track)],
                    TYPE_TO_ID[track.track_type],
                    0,
                    track.time_base.num,
                    track.time_base.den,
                ),
            )
            _write(crc, fh, TRACK_TAIL_STRUCT.pack(len(track.samples), len(name)))
            _write(crc, fh, name)
        for _, tid, _, sample in heapq.merge(*streams):
            flags = FLAG_KEYFRAME if sample.keyframe else 0
            _write(
                crc,
                fh,
                PACKET_HEADER_STRUCT.pack(
                    tid, flags, 0, sample.pts, sample.dts, sample.duration, len(sample.data)
                ),
            )
            _write(crc, fh, sample.data)
        fh.write(TRAILER_STRUCT.pack(crc[0], END_MAGIC))
    finally:
        if own_fh is not None:
            own_fh.close()
    return analysis
