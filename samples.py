"""构造确定性测试素材（不依赖任何第三方库）。

* 音频：48 kHz，每帧 1024 tick，时间基 1/48000；
* 视频：时间基 1/90000，帧率由 frame_ticks 决定（如 30000/1001 -> 3003 tick）。
* payload 由 random.Random(seed) 生成，种子由 (track, index) 决定，可复现对拍。
"""

from __future__ import annotations

import random

from avmux import MediaFile, Sample, TimeBase, Track

AUDIO_TIME_BASE = TimeBase(1, 48000)
AUDIO_FRAME_TICKS = 1024          # 每帧 1024/48000 s = 21.333... ms
AUDIO_PAYLOAD_SIZE = 32

VIDEO_TIME_BASE = TimeBase(1, 90000)
VIDEO_FRAME_TICKS = 3003          # 30000/1001 fps
VIDEO_PAYLOAD_SIZE = 64


_rng = random.Random(0xA7C1_0051)  # 固定种子，顺序生成，跨运行可复现


def _payload(track_tag: str, index: int, size: int) -> bytes:
    return _rng.randbytes(size)


def make_audio_track(
    n_frames: int,
    time_base: TimeBase = AUDIO_TIME_BASE,
    frame_ticks: int = AUDIO_FRAME_TICKS,
    payload_size: int = AUDIO_PAYLOAD_SIZE,
    start_ticks: int = 0,
    name: str = "aac-main",
) -> Track:
    track = Track(track_type="audio", time_base=time_base, name=name)
    for i in range(n_frames):
        pts = start_ticks + i * frame_ticks
        track.samples.append(
            Sample(
                pts=pts,
                dts=pts,
                duration=frame_ticks,
                data=_payload("audio", i, payload_size),
                keyframe=False,
            )
        )
    return track


def make_video_track(
    n_frames: int,
    time_base: TimeBase = VIDEO_TIME_BASE,
    frame_ticks: int = VIDEO_FRAME_TICKS,
    payload_size: int = VIDEO_PAYLOAD_SIZE,
    start_ticks: int = 0,
    name: str = "h264-main",
) -> Track:
    track = Track(track_type="video", time_base=time_base, name=name)
    for i in range(n_frames):
        pts = start_ticks + i * frame_ticks
        track.samples.append(
            Sample(
                pts=pts,
                dts=pts,
                duration=frame_ticks,
                data=_payload("video", i, payload_size),
                keyframe=(i % 30 == 0),
            )
        )
    return track


def make_media(
    audio_frames: int,
    video_frames: int,
    audio_time_base: TimeBase = AUDIO_TIME_BASE,
    video_time_base: TimeBase = VIDEO_TIME_BASE,
    audio_frame_ticks: int = AUDIO_FRAME_TICKS,
    video_frame_ticks: int = VIDEO_FRAME_TICKS,
    audio_payload_size: int = AUDIO_PAYLOAD_SIZE,
    video_payload_size: int = VIDEO_PAYLOAD_SIZE,
    audio_start_ticks: int = 0,
    video_start_ticks: int = 0,
) -> MediaFile:
    return MediaFile(
        tracks=[
            make_audio_track(
                audio_frames, audio_time_base, audio_frame_ticks,
                audio_payload_size, audio_start_ticks,
            ),
            make_video_track(
                video_frames, video_time_base, video_frame_ticks,
                video_payload_size, video_start_ticks,
            ),
        ]
    )
