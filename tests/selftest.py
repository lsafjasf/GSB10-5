"""Self-test: runs every fixture x every target ratio, asserts invariants,
and writes a JSON report with per-image criterion values, crop boxes and
out-of-bounds check data.

Usage: python3 tests/selftest.py [--report reports/selftest_report.json]
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from smartcrop.analysis import analyze
from smartcrop.cropper import auto_crop
from smartcrop.image import crop_image, load_image, save_ppm
from tests.make_fixtures import build_all

RATIOS = {"1:1": 1.0, "4:3": 4 / 3, "3:4": 3 / 4, "16:9": 16 / 9,
          "9:16": 9 / 16, "21:9": 21 / 9, "9:21": 9 / 21}

EXPECT_FOUND = {
    "center_subject": True, "edge_subject": True, "corner_subject": True,
    "no_subject_noise": False, "no_subject_flat": False,
    "tiny_subject": True, "wide_subject": True, "tall_subject": True,
}

# fixtures with a small subject flush against the border: the crop window is
# always big enough to contain them, so they must never be cut.
EDGE_FIXTURES = {"edge_subject", "corner_subject"}

REPORT_DIR = os.path.join(os.path.dirname(__file__), "..", "reports")


def _metrics_dict(r):
    m = r.metrics
    return {
        "grid": [m.grid_w, m.grid_h],
        "score_mean": round(m.score_mean, 4),
        "score_max": round(m.score_max, 4),
        "threshold": round(m.threshold, 4),
        "hot_cells": m.hot_cells,
        "component_cells": m.component_cells,
        "edge_density": round(m.edge_density, 4),
        "contrast_mean": round(m.contrast_mean, 4),
        "saliency_mean": round(m.saliency_mean, 4),
        "compactness": round(m.compactness, 4),
        "dominance": round(m.dominance, 4),
        "area_fraction": round(m.area_fraction, 4),
        "confidence": round(m.confidence, 4),
    }


def run(report_path=None, save_crops=True):
    paths = build_all()
    failures = []
    report = {"cases": [], "summary": {}}
    crop_dir = os.path.join(REPORT_DIR, "crops")
    if save_crops:
        os.makedirs(crop_dir, exist_ok=True)

    def check(cond, msg):
        if not cond:
            failures.append(msg)

    for name, path in sorted(paths.items()):
        img = load_image(path)
        sub = analyze(img)
        check(sub.found == EXPECT_FOUND[name],
              "%s: expected found=%s got %s" % (name, EXPECT_FOUND[name], sub.found))
        case = {
            "image": name, "width": img.width, "height": img.height,
            "subject_found": sub.found, "reason": sub.reason,
            "confidence": round(sub.confidence, 4),
            "subject_bbox": list(sub.bbox),
            "subject_centroid": [round(sub.centroid[0], 1), round(sub.centroid[1], 1)],
            "touches_edge": sub.touches_edge,
            "criterion": _metrics_dict(sub),
            "crops": [],
        }
        for rname, ratio in RATIOS.items():
            res = auto_crop(img, ratio, fallback="center")
            bc = res.bounds_check
            check(bc["all_inside"],
                  "%s %s: crop out of bounds %s" % (name, rname, bc))
            if res.mode != "reject":
                err = abs(res.width / res.height - ratio) / ratio
                check(err <= 0.02 + 2.0 / res.height,
                      "%s %s: ratio error %.4f" % (name, rname, err))
            if sub.found and res.mode == "subject":
                # subject must not be cut whenever the window can contain it
                sx0, sy0, sw, sh = sub.bbox
                fits = sw <= res.width and sh <= res.height
                if fits:
                    check(res.subject_coverage >= 0.999,
                          "%s %s: subject cut, coverage=%.3f" % (name, rname, res.subject_coverage))
                else:
                    # extreme ratio: centroid must stay inside the crop
                    cx, cy = sub.centroid
                    check(res.x <= cx < res.x + res.width and res.y <= cy < res.y + res.height,
                          "%s %s: centroid outside crop" % (name, rname))
            if name in EDGE_FIXTURES and sub.found:
                sx0, sy0, sw, sh = sub.bbox
                if sw <= res.width and sh <= res.height:
                    check(res.subject_coverage >= 0.999,
                          "%s %s: edge-hugging subject cut (coverage=%.3f)"
                          % (name, rname, res.subject_coverage))
            if not sub.found:
                check(res.mode == "center-fallback",
                      "%s %s: expected center fallback, got %s" % (name, rname, res.mode))
                cx = img.width / 2.0
                cy = img.height / 2.0
                check(abs((res.x + res.width / 2) - cx) <= 1.5
                      and abs((res.y + res.height / 2) - cy) <= 1.5,
                      "%s %s: fallback not centered" % (name, rname))
            case["crops"].append({
                "ratio": rname,
                "mode": res.mode, "reason": res.reason,
                "crop": {"x": res.x, "y": res.y, "w": res.width, "h": res.height},
                "ratio_error": round(res.ratio_error, 6),
                "subject_fully_inside": res.subject_inside,
                "subject_coverage": round(res.subject_coverage, 4),
                "bounds_check": bc,
            })
            if save_crops and res.mode != "reject":
                out = os.path.join(crop_dir, "%s_%s.ppm" % (name, rname.replace(":", "x")))
                save_ppm(crop_image(img, (res.x, res.y, res.width, res.height)), out)

        # reject fallback must deterministically refuse when no subject
        if not sub.found:
            rej = auto_crop(img, 1.0, fallback="reject")
            check(rej.mode == "reject", "%s: reject fallback failed" % name)
            case["reject_fallback"] = {"mode": rej.mode, "reason": rej.reason}
        report["cases"].append(case)

    n_crops = sum(len(c["crops"]) for c in report["cases"])
    report["summary"] = {
        "images": len(report["cases"]),
        "crop_cases": n_crops,
        "bounds_violations": sum(
            1 for c in report["cases"] for cr in c["crops"]
            if not cr["bounds_check"]["all_inside"]),
        "edge_subject_cut_cases": sum(
            1 for c in report["cases"] if c["image"] in EDGE_FIXTURES
            for cr in c["crops"]
            if c["subject_bbox"][2] <= cr["crop"]["w"]
            and c["subject_bbox"][3] <= cr["crop"]["h"]
            and cr["subject_coverage"] < 0.999),
        "assertion_failures": len(failures),
    }
    if report_path is None:
        report_path = os.path.join(REPORT_DIR, "selftest_report.json")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print("images=%d crop_cases=%d bounds_violations=%d edge_cut=%d failures=%d"
          % (report["summary"]["images"], n_crops,
             report["summary"]["bounds_violations"],
             report["summary"]["edge_subject_cut_cases"], len(failures)))
    for msg in failures:
        print("FAIL:", msg)
    print("report:", report_path)
    return 1 if failures else 0


if __name__ == "__main__":
    rp = None
    if "--report" in sys.argv:
        rp = sys.argv[sys.argv.index("--report") + 1]
    sys.exit(run(rp))
