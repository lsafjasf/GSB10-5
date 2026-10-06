"""CLI: python3 -m thumb input.png output.png WxH [--kernel ...]
            [--sharpen after|before|none] [--no-cover]
"""

import argparse

from .pngcodec import read_png, write_png
from .pipeline import make_thumbnail, Policy


def main(argv=None):
    ap = argparse.ArgumentParser(description="Pure-stdlib thumbnailer")
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("box", help="max WxH, e.g. 160x120")
    ap.add_argument("--kernel", default="triangle",
                    choices=["box", "triangle", "lanczos3"])
    ap.add_argument("--sharpen", default="after",
                    choices=["after", "before", "none"])
    ap.add_argument("--amount", type=float, default=0.6)
    ap.add_argument("--min-side", type=int, default=8)
    args = ap.parse_args(argv)
    bw, bh = map(int, args.box.lower().split("x"))
    img = read_png(args.input)
    thumb = make_thumbnail(
        img, bw, bh, policy=Policy(min_side=args.min_side),
        kernel=args.kernel,
        sharpen=None if args.sharpen == "none" else args.sharpen,
        amount=args.amount,
    )
    write_png(args.output, thumb)
    print("%s: %dx%d -> %dx%d" % (args.output, img.width, img.height,
                                  thumb.width, thumb.height))


if __name__ == "__main__":
    main()
