"""Target-ratio crop window placement with deterministic fallback."""
from __future__ import annotations

from dataclasses import dataclass

from .analysis import analyze


@dataclass
class CropResult:
    x: int
    y: int
    width: int
    height: int
    target_ratio: float
    ratio_error: float
    mode: str                 # "subject" | "center-fallback" | "reject"
    reason: str
    subject_found: bool
    subject_confidence: float
    subject_bbox: tuple
    subject_inside: bool      # subject bbox fully contained in crop
    subject_coverage: float   # fraction of subject bbox area inside crop
    bounds_check: dict


def _target_size(img_w, img_h, ratio):
    """Largest integer (w, h) with w/h ~= ratio and w<=img_w, h<=img_h.

    Among all integer sizes with relative ratio error <= max(2%, 1.5x the
    best achievable error), pick the largest area, so a subject that can be
    fully contained is not cut just to shave ratio error.
    """
    base_w = img_w
    base_h = int(img_w / ratio + 0.5)
    if base_h > img_h:
        base_h = img_h
        base_w = int(img_h * ratio + 0.5)
    candidates = []
    min_err = None
    for w in range(base_w, 0, -1):
        h = int(w / ratio + 0.5)
        if h < 1:
            h = 1
        if h > img_h or w > img_w:
            continue
        err = abs(w / h - ratio) / ratio
        candidates.append((w, h, err))
        if min_err is None or err < min_err:
            min_err = err
    tol = max(0.02, min_err * 1.5)
    ok = [c for c in candidates if c[2] <= tol]
    w, h, err = max(ok, key=lambda c: (c[0] * c[1], -c[2]))
    return w, h, err


def _bounds_check(x, y, w, h, img_w, img_h):
    return {
        "x>=0": x >= 0,
        "y>=0": y >= 0,
        "x+w<=img_w": x + w <= img_w,
        "y+h<=img_h": y + h <= img_h,
        "w>0": w > 0,
        "h>0": h > 0,
        "x": x, "y": y, "w": w, "h": h,
        "img_w": img_w, "img_h": img_h,
        "overflow_left_px": max(0, -x),
        "overflow_top_px": max(0, -y),
        "overflow_right_px": max(0, x + w - img_w),
        "overflow_bottom_px": max(0, y + h - img_h),
        "all_inside": 0 <= x and 0 <= y and x + w <= img_w and y + h <= img_h
                      and w > 0 and h > 0,
    }


def _clamp_window(x, y, w, h, img_w, img_h):
    if x < 0:
        x = 0
    if y < 0:
        y = 0
    if x + w > img_w:
        x = img_w - w
    if y + h > img_h:
        y = img_h - h
    return x, y


def _coverage(sub, box):
    sx0, sy0, sw, sh = sub
    x0, y0, w, h = box
    ix0 = max(sx0, x0)
    iy0 = max(sy0, y0)
    ix1 = min(sx0 + sw, x0 + w)
    iy1 = min(sy0 + sh, y0 + h)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    return (ix1 - ix0) * (iy1 - iy0) / float(sw * sh)


def auto_crop(image, target_ratio, fallback="center", max_grid=64):
    """Compute a crop window for target_ratio (width/height).

    fallback: "center" -> center crop when no subject is found;
              "reject" -> return mode="reject" instead of cropping.
    """
    img_w, img_h = image.width, image.height
    sub = analyze(image, max_grid=max_grid)
    cw, ch, err = _target_size(img_w, img_h, target_ratio)

    def build(x, y, mode, reason, sub_found, conf, sbox):
        x = int(x)
        y = int(y)
        bc = _bounds_check(x, y, cw, ch, img_w, img_h)
        inside = False
        cov = 0.0
        if sbox:
            sx0, sy0, sw, sh = sbox
            inside = (sx0 >= x and sy0 >= y and sx0 + sw <= x + cw and sy0 + sh <= y + ch)
            cov = _coverage(sbox, (x, y, cw, ch))
        return CropResult(x, y, cw, ch, target_ratio, err, mode, reason,
                          sub_found, conf, sbox, inside, cov, bc)

    if not sub.found:
        if fallback == "reject":
            bc = _bounds_check(0, 0, 0, 0, img_w, img_h)
            return CropResult(0, 0, 0, 0, target_ratio, 0.0, "reject",
                              sub.reason, False, sub.confidence, (0, 0, 0, 0),
                              False, 0.0, bc)
        x = (img_w - cw) // 2
        y = (img_h - ch) // 2
        return build(x, y, "center-fallback", sub.reason, False, sub.confidence, None)

    sx0, sy0, sw, sh = sub.bbox
    scx, scy = sub.centroid

    if sw <= cw and sh <= ch:
        # window can contain the whole subject: center on its centroid,
        # then clamp into image; clamping guarantees edge-hugging
        # subjects are never cut (window is large enough).
        x = int(scx - cw / 2.0 + 0.5)
        y = int(scy - ch / 2.0 + 0.5)
        x, y = _clamp_window(x, y, cw, ch, img_w, img_h)
        box = (x, y, cw, ch)
        cov = _coverage(sub.bbox, box)
        inside = cov >= 0.999
        reason = "ok" if inside else "partial-cover-extreme-ratio"
        return build(x, y, "subject", reason, True, sub.confidence, sub.bbox)

    # window smaller than the subject: keep the centroid in frame and
    # maximize overlap (extreme ratio case; full contain is impossible).
    x = int(scx - cw / 2.0 + 0.5)
    y = int(scy - ch / 2.0 + 0.5)
    x, y = _clamp_window(x, y, cw, ch, img_w, img_h)
    return build(x, y, "subject", "subject-larger-than-window", True,
                 sub.confidence, sub.bbox)
