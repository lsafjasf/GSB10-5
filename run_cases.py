"""Run the required stitching scenarios and dump all reported data.

Run:  python3 run_cases.py [outdir]     (default outdir: out/)
"""

import json
import os
import sys

from seamstitch.stitch import stitch
from seamstitch.png import save_png
from seamstitch import synth


def build_cases():
    cases = {}

    # 1. normal case: subject inside the overlap, seam should route around it
    scene = synth.gradient_scene(300, 90)
    synth.add_noise(scene, 3, seed=7)
    synth.add_disc(scene, 150, 45, 16, (12, 12, 12))       # subject
    left, right = synth.make_pair(scene, 180, 90)          # overlap 90 (50%)
    cases["normal"] = (left, right, 90, {})

    # 2. large overlap (73%)
    scene = synth.gradient_scene(320, 80)
    synth.add_disc(scene, 130, 40, 14, (15, 15, 15))
    left, right = synth.make_pair(scene, 280, 40)          # overlap 240
    cases["large_overlap"] = (left, right, 40, {})

    # 3. small overlap -> deterministic fallback (blend) and reject
    scene = synth.gradient_scene(260, 70)
    left, right = synth.make_pair(scene, 130, 126)         # overlap 4
    cases["small_overlap_blend"] = (left, right, 126, {"on_too_small": "blend"})
    cases["small_overlap_reject"] = (left, right, 126, {"on_too_small": "reject"})

    # 4. strong exposure difference (right view 1.6x brighter)
    scene = synth.gradient_scene(300, 90)
    synth.add_noise(scene, 2, seed=11)
    synth.add_disc(scene, 145, 45, 15, (10, 10, 10))
    left, right = synth.make_pair(scene, 180, 100, gain_right=1.6)
    cases["exposure_difference"] = (left, right, 100, {})

    # 5. seam is forced through the subject (bar spans the whole overlap)
    scene = synth.gradient_scene(280, 90)
    synth.add_striped_bar(scene, 80, 180)
    left, right = synth.make_pair(scene, 180, 80)          # overlap cols 80..179
    cases["forced_subject"] = (left, right, 80, {})

    return cases


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else "out"
    os.makedirs(outdir, exist_ok=True)
    seams, brightness, summary = {}, {}, []
    for name, (left, right, offset, kw) in build_cases().items():
        res = stitch(left, right, offset, **kw)
        if res.image is not None:
            res.image.save_ppm(os.path.join(outdir, f"{name}.ppm"))
            save_png(res.image, os.path.join(outdir, f"{name}.png"))
        seams[name] = {
            "status": res.status,
            "overlap": res.overlap,
            "offset_x": res.offset_x,
            "seam_x_per_row_overlap_local": res.seam or None,
            "seam_cost_total": round(res.seam_cost_total, 2),
            "crossings_pixels": res.crossings_pixels,
            "crossings_segments": res.crossings_segments,
        }
        brightness[name] = {
            "mean_lum_left_before": round(res.mean_lum_left, 2),
            "mean_lum_right": round(res.mean_lum_right, 2),
            "gain_applied_to_left": round(res.gain_applied_to_left, 4),
            "mean_abs_lum_diff_before": round(res.lum_diff_before, 2),
            "mean_abs_lum_diff_after": round(res.lum_diff_after, 2),
        }
        summary.append((name, res))
        print(f"[{name:22s}] status={res.status:14s} overlap={res.overlap:3d} "
              f"crossings={res.crossings_pixels:3d}px/{res.crossings_segments}seg "
              f"lumdiff {res.lum_diff_before:6.2f} -> {res.lum_diff_after:6.2f}")

    with open(os.path.join(outdir, "seams.json"), "w") as fh:
        json.dump(seams, fh, indent=1)
    with open(os.path.join(outdir, "brightness.json"), "w") as fh:
        json.dump(brightness, fh, indent=1)
    print(f"\nwrote {outdir}/seams.json, {outdir}/brightness.json and "
          f"{sum(1 for _, r in summary if r.image is not None)} PPM images")


if __name__ == "__main__":
    main()
