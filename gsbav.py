"""gsbav —— 简易音视频封装/解封装库（仅 Python 标准库）。

核心约定：
- 每条轨迹携带独立时间基 TimeBase(num, den)，时间戳为整数 tick。
- 跨轨迹比较一律先换算到“秒”（fractions.Fraction 精确有理数），
  绝不直接比较两条轨迹的原始 tick 数值。
- tick -> 秒: seconds = tick * num / den （精确，无取整）
- 秒/tick 跨时间基换算: rescale_q()，默认 NEAR_INF（四舍五入、平局远离零，
  与 FFmpeg AV_ROUND_NEAR_INF 一致）。
"""
from __future__ import annotations

import heapq
import struct
from dataclasses import dataclass, field
from enum import Enum
from fractions import Fraction

MAGIC = b"GSBAV001"
VERSION = 1
KIND_VIDEO = 1
KIND_AUDIO = 2
KIND_NAMES = {KIND_VIDEO: "video", KIND_AUDIO: "audio"}
END_TRACK_ID = 0xFFFFFFFF

_FILE_HDR = struct.Struct("<8sII")           # magic, version, track_count
_TRACK_HDR = struct.Struct("<IBIIH")         # track_id, kind, tb_num, tb_den, codec_len
_SAMPLE_HDR = struct.Struct("<IqqqIQ")       # track_id, pts, dts, duration, flags, size
_FLAG_KEYFRAME = 1


class Rounding(Enum):
    FLOOR = "floor"        # 向负无穷取整
    CEIL = "ceil"          # 向正无穷取整
    NEAR_INF = "near_inf"  # 四舍五入，平局远离零（默认，同 FFmpeg）


@dataclass(frozen=True)
class TimeBase:
    num: int
    den: int

    def to_fraction(self) -> Fraction:
        return Fraction(self.num, self.den)


def rescale_q(value: int, src: TimeBase, dst: TimeBase,
              rounding: Rounding = Rounding.NEAR_INF) -> int:
    """把 src 时间基下的整数 tick 换算为 dst 时间基下的整数 tick。

    value * src.num * dst.den / (src.den * dst.num)，按 rounding 取整。
    """
    num = value * src.num * dst.den
    den = src.den * dst.num
    if rounding is Rounding.FLOOR:
        return num // den
    if rounding is Rounding.CEIL:
        return -((-num) // den)
    q, r = divmod(abs(num), den)
    if 2 * r >= den:
        q += 1
    return q if num >= 0 else -q


def tick_to_seconds(tick: int, tb: TimeBase) -> Fraction:
    """tick 转秒，精确有理数，无取整。"""
    return Fraction(tick * tb.num, tb.den)


@dataclass
class Sample:
    track_id: int
    pts: int
    dts: int
    duration: int
    keyframe: bool
    data: bytes


@dataclass
class TrackReport:
    track_id: int
    kind: int
    time_base: TimeBase
    codec: str
    nb_samples: int
    start_pts: int          # 首样本 pts（轨迹时间基）
    end_pts: int            # max(pts + duration)（轨迹时间基）
    start_sec: Fraction     # 精确秒
    end_sec: Fraction

    @property
    def duration_sec(self) -> Fraction:
        return self.end_sec - self.start_sec

    @property
    def kind_name(self) -> str:
        return KIND_NAMES.get(self.kind, str(self.kind))


@dataclass
class MuxReport:
    tracks: list = field(default_factory=list)
    intersection_start_sec: object = None   # Fraction，各轨迹共同覆盖区间起点
    intersection_end_sec: object = None     # Fraction，共同覆盖区间终点
    warnings: list = field(default_factory=list)


def _build_report(track_stats, expected_kinds=()) -> MuxReport:
    tracks = [s for s in track_stats if s is not None and s.nb_samples > 0]
    rep = MuxReport(tracks=tracks)

    present = {t.kind for t in tracks}
    for kind in expected_kinds:
        if kind not in present:
            rep.warnings.append(f"MISSING_TRACK: {KIND_NAMES.get(kind, kind)}")

    if tracks:
        rep.intersection_start_sec = max(t.start_sec for t in tracks)
        rep.intersection_end_sec = min(t.end_sec for t in tracks)
        if rep.intersection_start_sec >= rep.intersection_end_sec:
            rep.warnings.append(
                "NO_OVERLAP: 各轨迹时间范围无交集 "
                f"[{float(rep.intersection_start_sec):.6f}, "
                f"{float(rep.intersection_end_sec):.6f})")

    if len(tracks) > 1:
        durs = [t.duration_sec for t in tracks]
        spread = max(durs) - min(durs)
        # 容差：各轨迹自身一个采样帧时长中的最大值，超出即报告，绝不静默补齐
        tol = max(
            Fraction(t.time_base.num * (t.end_pts - t.start_pts), t.time_base.den)
            / t.nb_samples for t in tracks)
        if spread > tol:
            detail = ", ".join(
                f"{t.kind_name}={float(t.duration_sec):.6f}s" for t in tracks)
            rep.warnings.append(
                f"DURATION_MISMATCH: {detail}, "
                f"diff={float(spread):.6f}s > tolerance={float(tol):.6f}s")
    return rep


class Muxer:
    """流式封装器：样本随写随落盘，内存占用与素材时长无关。"""

    def __init__(self, path, expected_kinds=()):
        self._fh = open(path, "wb")
        self._expected = tuple(expected_kinds)
        self._track_defs = []      # (kind, TimeBase, codec)
        self._stats = []           # 每轨迹运行统计
        self._header_written = False
        self.report = None

    def add_track(self, kind: int, time_base: TimeBase, codec: str = "raw") -> int:
        if self._header_written:
            raise RuntimeError("已开始写样本，不能再添加轨迹")
        tid = len(self._track_defs)
        self._track_defs.append((kind, time_base, codec))
        self._stats.append({
            "nb": 0, "start": None, "end": None,
        })
        return tid

    def _write_header(self):
        self._fh.write(_FILE_HDR.pack(MAGIC, VERSION, len(self._track_defs)))
        for tid, (kind, tb, codec) in enumerate(self._track_defs):
            c = codec.encode("utf-8")
            self._fh.write(_TRACK_HDR.pack(tid, kind, tb.num, tb.den, len(c)))
            self._fh.write(c)
        self._header_written = True

    def write_sample(self, track_id: int, pts: int, dts: int, duration: int,
                     data: bytes, keyframe: bool = False):
        if not self._header_written:
            self._write_header()
        flags = _FLAG_KEYFRAME if keyframe else 0
        self._fh.write(_SAMPLE_HDR.pack(track_id, pts, dts, duration, flags, len(data)))
        self._fh.write(data)
        st = self._stats[track_id]
        st["start"] = pts if st["start"] is None else min(st["start"], pts)
        st["end"] = pts + duration if st["end"] is None else max(st["end"], pts + duration)
        st["nb"] += 1

    def close(self) -> MuxReport:
        if not self._header_written:
            self._write_header()
        self._fh.write(_SAMPLE_HDR.pack(END_TRACK_ID, 0, 0, 0, 0, 0))
        self._fh.close()
        stats = []
        for tid, ((kind, tb, codec), st) in enumerate(zip(self._track_defs, self._stats)):
            if st["nb"] == 0:
                stats.append(None)
                continue
            stats.append(TrackReport(
                track_id=tid, kind=kind, time_base=tb, codec=codec,
                nb_samples=st["nb"], start_pts=st["start"], end_pts=st["end"],
                start_sec=tick_to_seconds(st["start"], tb),
                end_sec=tick_to_seconds(st["end"], tb)))
        self.report = _build_report(stats, self._expected)
        return self.report

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class Demuxer:
    """流式解封装器：samples() 逐条产出，不全量载入内存。"""

    def __init__(self, path):
        self.path = path
        with open(path, "rb") as fh:
            magic, version, ntracks = _FILE_HDR.unpack(fh.read(_FILE_HDR.size))
            if magic != MAGIC:
                raise ValueError(f"不是 gsbav 文件: {path}")
            if version != VERSION:
                raise ValueError(f"不支持的版本: {version}")
            self.tracks = []
            for _ in range(ntracks):
                tid, kind, tbn, tbd, clen = _TRACK_HDR.unpack(fh.read(_TRACK_HDR.size))
                codec = fh.read(clen).decode("utf-8")
                self.tracks.append({
                    "track_id": tid, "kind": kind,
                    "time_base": TimeBase(tbn, tbd), "codec": codec,
                })

    def samples(self):
        with open(self.path, "rb") as fh:
            fh.seek(_FILE_HDR.size)
            for t in self.tracks:
                fh.seek(_TRACK_HDR.size + len(t["codec"].encode("utf-8")), 1)
            while True:
                raw = fh.read(_SAMPLE_HDR.size)
                if len(raw) < _SAMPLE_HDR.size:
                    raise ValueError("文件截断：缺少结束记录")
                tid, pts, dts, dur, flags, size = _SAMPLE_HDR.unpack(raw)
                if tid == END_TRACK_ID:
                    return
                data = fh.read(size)
                if len(data) < size:
                    raise ValueError("文件截断：样本数据不完整")
                yield Sample(tid, pts, dts, dur, bool(flags & _FLAG_KEYFRAME), data)

    def report(self, expected_kinds=()) -> MuxReport:
        stats = [None] * len(self.tracks)
        for s in self.samples():
            st = stats[s.track_id]
            if st is None:
                st = stats[s.track_id] = {"nb": 0, "start": None, "end": None}
            st["start"] = s.pts if st["start"] is None else min(st["start"], s.pts)
            st["end"] = s.pts + s.duration if st["end"] is None else max(st["end"], s.pts + s.duration)
            st["nb"] += 1
        tres = []
        for t, st in zip(self.tracks, stats):
            if st is None:
                tres.append(None)
                continue
            tb = t["time_base"]
            tres.append(TrackReport(
                track_id=t["track_id"], kind=t["kind"], time_base=tb,
                codec=t["codec"], nb_samples=st["nb"],
                start_pts=st["start"], end_pts=st["end"],
                start_sec=tick_to_seconds(st["start"], tb),
                end_sec=tick_to_seconds(st["end"], tb)))
        return _build_report(tres, expected_kinds)


def interleave(iterables_with_tb):
    """按 dts 的真实秒数归并多条轨迹的样本流（跨时间基安全）。

    iterables_with_tb: [(TimeBase, iterable_of_Sample), ...]
    比较键为 Fraction(dts) * time_base，绝不直接比较原始 tick。
    """
    heap = []
    for idx, (tb, it) in enumerate(iterables_with_tb):
        it = iter(it)
        try:
            s = next(it)
        except StopIteration:
            continue
        heapq.heappush(heap, (tick_to_seconds(s.dts, tb), idx, s, it, tb))
    while heap:
        _, idx, s, it, tb = heapq.heappop(heap)
        yield s
        try:
            nxt = next(it)
        except StopIteration:
            continue
        heapq.heappush(heap, (tick_to_seconds(nxt.dts, tb), idx, nxt, it, tb))


def remux(src_path, dst_path, expected_kinds=()) -> MuxReport:
    """解封装 src 再封装到 dst，返回目标文件报告。"""
    demux = Demuxer(src_path)
    with Muxer(dst_path, expected_kinds=expected_kinds) as mux:
        for t in demux.tracks:
            mux.add_track(t["kind"], t["time_base"], t["codec"])
        for s in demux.samples():
            mux.write_sample(s.track_id, s.pts, s.dts, s.duration, s.data, s.keyframe)
    return mux.report
