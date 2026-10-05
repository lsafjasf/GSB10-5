"""对拍演示：封装 -> 解封装 -> 再封装，逐样本比对并打印数据。

运行：python3 demo_roundtrip.py
"""

from __future__ import annotations

import hashlib
import io

from avmux import demux, mux
import samples


def digest(track):
    h = hashlib.sha256()
    for s in track.samples:
        h.update(s.pts.to_bytes(8, "little", signed=True))
        h.update(s.dts.to_bytes(8, "little", signed=True))
        h.update(s.duration.to_bytes(4, "little"))
        h.update(bytes([s.keyframe]))
        h.update(s.data)
    return h.hexdigest()[:16]


def table(media, label):
    print(f"  [{label}]")
    for t in media.tracks:
        s0, s1 = t.samples[0], t.samples[-1]
        print(
            f"    {t.track_type:5s} tb={t.time_base} n={len(t.samples)} "
            f"first(pts={s0.pts},dts={s0.dts},dur={s0.duration},kf={s0.keyframe},"
            f"data={s0.data[:8].hex()}..) "
            f"last(pts={s1.pts},dts={s1.dts},dur={s1.duration}) "
            f"sha256(all)={digest(t)}"
        )


def main():
    media = samples.make_media(audio_frames=47, video_frames=30)
    print("== 原始媒体 ==")
    table(media, "original")

    buf1 = io.BytesIO()
    rep1 = mux(media, buf1)
    print(f"\n== 封装 #1 ==  文件大小 {buf1.tell()} 字节")
    for i in rep1.issues:
        print(f"  [{i.severity}] {i.code}: {i.message}")

    buf1.seek(0)
    media2, rep2 = demux(buf1)
    print("\n== 解封装 ==")
    table(media2, "demuxed")

    buf2 = io.BytesIO()
    mux(media2, buf2)
    print(f"\n== 再封装 #2 ==  文件大小 {buf2.tell()} 字节")

    same_bytes = buf1.getvalue() == buf2.getvalue()
    print(f"\n两次封装字节级一致: {same_bytes}")

    ok = True
    for t1, t2 in zip(media.tracks, media2.tracks):
        for s1, s2 in zip(t1.samples, t2.samples):
            if (s1.pts, s1.dts, s1.duration, s1.keyframe, s1.data) != (
                s2.pts, s2.dts, s2.duration, s2.keyframe, s2.data
            ):
                ok = False
    print(f"逐样本对拍（pts/dts/duration/keyframe/payload）: {'全部一致' if ok else '不一致!'}")


if __name__ == "__main__":
    main()
