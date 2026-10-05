"""交叉计算演示：不同时间基下，音视频轨迹起止时间的交叉区间。

运行：python3 demo_crossing.py
"""

from __future__ import annotations

from fractions import Fraction

from avmux import TimeBase, analyze
import samples


def fmt_frac(x: Fraction) -> str:
    return f"{float(x):.6f} ({x})"


def show(title, media):
    print(f"\n=== {title} ===")
    rep = analyze(media)
    for s in rep.spans:
        print(
            f"  {s.track_type:5s} tb={s.time_base}  "
            f"ticks [{s.start_ticks}, {s.end_ticks})  "
            f"us [{s.start_us}, {s.end_us})  "
            f"exact_end_us={fmt_frac(s.end_us_exact)}"
        )
    for i in rep.issues:
        print(f"  [{i.severity:7s}] {i.code}: {i.message}")
    if rep.has_overlap:
        print(
            f"  交叉区间(公共us基): [{rep.overlap_start_us}, {rep.overlap_end_us}) "
            f"时长 {rep.overlap_duration_us} us"
        )
        print(
            f"  精确边界: start={fmt_frac(rep.overlap_start_us_exact)}  "
            f"end={fmt_frac(rep.overlap_end_us_exact)}"
        )
    else:
        print("  交叉区间: 无")


def main():
    # 情形1：双轨迹对齐（各 1.0s，时间基不同：1/1000 vs 1/90000）
    from avmux import MediaFile
    media = MediaFile(tracks=[
        samples.make_audio_track(50, time_base=TimeBase(1, 1000), frame_ticks=20),
        samples.make_video_track(30, time_base=TimeBase(1, 90000), frame_ticks=3000),
    ])
    show("双轨迹对齐 1.0s（tb 1/1000 vs 1/90000）", media)

    # 情形2：真实时间基 1/48000 vs 1/90000@29.97fps，时长天然不一致
    media = samples.make_media(audio_frames=47, video_frames=30)
    show("48k 音频 47 帧 vs 90k 视频 30 帧@29.97fps（时长不一致）", media)

    # 情形3：音频起点偏移 5ms
    media = samples.make_media(
        audio_frames=10, video_frames=6,
        audio_time_base=TimeBase(1, 1000), audio_frame_ticks=10,
        video_time_base=TimeBase(1, 90000), video_frame_ticks=3000,
        audio_start_ticks=5,
    )
    show("音频晚 5ms 开始（起点交叉）", media)

    # 情形4：完全不重叠
    media = samples.make_media(
        audio_frames=5, video_frames=5,
        audio_time_base=TimeBase(1, 1000), audio_frame_ticks=10,
        video_time_base=TimeBase(1, 1000), video_frame_ticks=10,
        video_start_ticks=1000,
    )
    show("音视频完全不重叠", media)


if __name__ == "__main__":
    main()
