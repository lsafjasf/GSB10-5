"""解封装（demux）：读取 AVMX1 文件，重建 MediaFile。

* 校验头/尾 magic 与 CRC32（损坏抛 FormatError）；
* 按轨迹还原时间基、样本（pts/dts/duration 为原始整数，data 为原始字节）；
* 还原后同样做轨迹分析（缺轨/时长不一致 -> 报告；strict=True 时 error 抛异常）；
  注意：本库不会“补齐”任何缺失轨迹或样本。
"""

from __future__ import annotations

import struct
import zlib
from typing import BinaryIO, Tuple, Union

from .format import (
    END_MAGIC,
    FLAG_KEYFRAME,
    FormatError,
    HEADER_STRUCT,
    HEADER_SIZE,
    ID_TO_TYPE,
    MAGIC,
    PACKET_HEADER_SIZE,
    PACKET_HEADER_STRUCT,
    TRAILER_SIZE,
    TRAILER_STRUCT,
    TRACK_FIXED_STRUCT,
    TRACK_TAIL_STRUCT,
    VERSION,
)
from .model import MediaFile, Sample, Track
from .report import Analysis, analyze
from .timebase import TimeBase


class DemuxError(Exception):
    pass


def _read_exact(fh: BinaryIO, n: int, what: str) -> bytes:
    data = fh.read(n)
    if len(data) != n:
        raise FormatError(f"unexpected EOF reading {what}: want {n}, got {len(data)}")
    return data


def demux(
    source: Union[str, BinaryIO], strict: bool = False, required=("audio", "video")
) -> Tuple[MediaFile, Analysis]:
    """解封装 source（路径或二进制文件对象）。返回 (MediaFile, 分析报告)。

    required：期望必须存在的轨迹类型；缺失只报告（strict 时抛错），不补齐。
    """
    own_fh = None
    if isinstance(source, (str, bytes)):
        own_fh = open(source, "rb")
        fh = own_fh
    else:
        fh = source
    try:
        blob = fh.read()
    finally:
        if own_fh is not None:
            own_fh.close()

    if len(blob) < HEADER_SIZE + TRAILER_SIZE:
        raise FormatError("file too small to be AVMX1")

    magic, version, _flags, _reserved, creation_time = HEADER_STRUCT.unpack(
        blob[:HEADER_SIZE]
    )
    if magic != MAGIC:
        raise FormatError(f"bad header magic: {magic!r}")
    if version != VERSION:
        raise FormatError(f"unsupported version: {version}")

    stored_crc, end_magic = TRAILER_STRUCT.unpack(blob[-TRAILER_SIZE:])
    if end_magic != END_MAGIC:
        raise FormatError(f"bad end magic: {end_magic!r}")
    actual_crc = zlib.crc32(blob[:-TRAILER_SIZE])
    if actual_crc != stored_crc:
        raise FormatError(f"crc mismatch: stored={stored_crc:#010x} actual={actual_crc:#010x}")

    pos = HEADER_SIZE
    track_count = int.from_bytes(blob[pos:pos + 2], "little")
    pos += 2

    tracks = []
    track_counts = {}
    for _ in range(track_count):
        tid, type_id, _rsv, tb_num, tb_den = TRACK_FIXED_STRUCT.unpack(
            blob[pos:pos + TRACK_FIXED_STRUCT.size]
        )
        pos += TRACK_FIXED_STRUCT.size
        sample_count, name_len = TRACK_TAIL_STRUCT.unpack(
            blob[pos:pos + TRACK_TAIL_STRUCT.size]
        )
        pos += TRACK_TAIL_STRUCT.size
        name = blob[pos:pos + name_len].decode("utf-8")
        pos += name_len
        if type_id not in ID_TO_TYPE:
            raise FormatError(f"unknown track type id: {type_id}")
        track = Track(
            track_type=ID_TO_TYPE[type_id],
            time_base=TimeBase(tb_num, tb_den),
            name=name,
        )
        tracks.append((tid, track))
        track_counts[tid] = sample_count
    track_by_id = {tid: track for tid, track in tracks}
    if len(track_by_id) != len(tracks):
        raise FormatError("duplicate track ids in track table")

    media = MediaFile(tracks=[t for _, t in tracks], creation_time=creation_time)

    body_end = len(blob) - TRAILER_SIZE
    seen = {tid: 0 for tid in track_by_id}
    while pos < body_end:
        if pos + PACKET_HEADER_SIZE > body_end:
            raise FormatError("truncated packet header")
        tid, flags, _rsv, pts, dts, duration, size = PACKET_HEADER_STRUCT.unpack(
            blob[pos:pos + PACKET_HEADER_SIZE]
        )
        pos += PACKET_HEADER_SIZE
        if tid not in track_by_id:
            raise FormatError(f"packet references unknown track id {tid}")
        data = blob[pos:pos + size]
        if len(data) != size:
            raise FormatError("truncated packet payload")
        pos += size
        track_by_id[tid].samples.append(
            Sample(
                pts=pts,
                dts=dts,
                duration=duration,
                data=data,
                keyframe=bool(flags & FLAG_KEYFRAME),
            )
        )
        seen[tid] += 1

    if pos != body_end:
        raise FormatError("packet body and trailer do not line up")
    for tid, expected in track_counts.items():
        if seen[tid] != expected:
            raise FormatError(
                f"sample count mismatch for track {tid}: "
                f"table={expected} packets={seen[tid]}"
            )

    analysis = analyze(media, require=required)
    if strict and not analysis.ok:
        msgs = "; ".join(i.message for i in analysis.issues if i.severity == "error")
        raise DemuxError(f"demuxed data invalid (strict): {msgs}")
    return media, analysis
