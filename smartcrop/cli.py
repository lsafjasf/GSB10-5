"""CLI: python3 -m smartcrop.cli INPUT --ratio W:H [--fallback center|reject] [--out OUT]"""
from __future__ import annotations

import argparse
import json
import sys

from .cropper import auto_crop
from .image import crop_image, load_image, save_ppm


def main(argv=None):
    parser = argparse.ArgumentParser(prog="smartcrop",
                                     description="subject-aware auto crop (stdlib only)")
    parser.add_argument("input", help="input image (PPM P6 / PGM P5 / BMP)")
    parser.add_argument("--ratio", required=True, help="target ratio, e.g. 16:9 or 1:1")
    parser.add_argument("--fallback", choices=["center", "reject"], default="center",
                        help="what to do when no subject is found (default: center)")
    parser.add_argument("--out", help="write cropped image (PPM/PGM)")
    parser.add_argument("--max-grid", type=int, default=64, help="analysis grid size")
    args = parser.parse_args(argv)

    try:
        rw, rh = args.ratio.split(":")
        ratio = float(rw) / float(rh)
    except ValueError:
        parser.error("--ratio must look like 16:9")
    if ratio <= 0:
        parser.error("ratio must be positive")

    image = load_image(args.input)
    result = auto_crop(image, ratio, fallback=args.fallback, max_grid=args.max_grid)
    report = {
        "input": args.input,
        "image": {"width": image.width, "height": image.height},
        "target_ratio": args.ratio,
        "mode": result.mode,
        "reason": result.reason,
        "subject": {
            "found": result.subject_found,
            "confidence": round(result.subject_confidence, 4),
            "bbox": result.subject_bbox,
            "fully_inside_crop": result.subject_inside,
            "coverage": round(result.subject_coverage, 4),
        },
        "crop": {"x": result.x, "y": result.y,
                 "width": result.width, "height": result.height,
                 "ratio_error": round(result.ratio_error, 6)},
        "bounds_check": result.bounds_check,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if args.out and result.mode != "reject":
        save_ppm(crop_image(image, (result.x, result.y, result.width, result.height)),
                 args.out)
        print("wrote %s" % args.out, file=sys.stderr)
    return 0 if result.mode != "reject" else 2


if __name__ == "__main__":
    sys.exit(main())
