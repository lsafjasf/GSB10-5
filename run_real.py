"""处理真实视频：从标准输入读取外部解码器输出的原始灰度流。

用法（需要系统里有 ffmpeg，仅负责解码；检测本身只用标准库）：

    ffmpeg -i input.mp4 -vf scale=48:27 -pix_fmt gray -f rawvideo - \
        | python3 run_real.py --width 48 --height 27 --fps 25

也可以直接给文件：

    python3 run_real.py --width 48 --height 27 --fps 25 raw.gray
"""

import argparse
import sys

from keyframe import KeyframeDetector
from keyframe.synthetic import RawGrayReader


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=int, required=True)
    ap.add_argument("--height", type=int, required=True)
    ap.add_argument("--fps", type=float, default=25.0)
    ap.add_argument("file", nargs="?", help="原始灰度文件，缺省读 stdin")
    args = ap.parse_args()

    if args.file:
        fobj = open(args.file, "rb")
    else:
        fobj = sys.stdin.buffer

    det = KeyframeDetector(args.width, args.height)
    n = 0
    for gray in RawGrayReader(fobj, args.width, args.height):
        det.feed(gray)
        n += 1
    events = det.finish()

    for e in events:
        if e.kind in ("cut", "gradual"):
            t = e.index / args.fps
            print(f"{e.kind:8s} 帧 {e.index:6d}  t={t:8.2f}s  "
                  f"持续 {e.duration} 帧")
    keys = [e for e in events if e.kind in ("cut", "gradual")]
    minutes = n / args.fps / 60
    print(f"\n共 {n} 帧（{minutes:.1f} 分钟），关键帧 {len(keys)} 个，"
          f"密度 {len(keys)/max(minutes,1e-9):.2f} 个/分钟",
          file=sys.stderr)


if __name__ == "__main__":
    main()
