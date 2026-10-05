"""Image stitching with exposure compensation, DP minimum-cost seam and
feathered blending.  Pure Python (standard library only).

Search method
-------------
The overlap region is a grid ``W x H`` (W = overlap width, H = image height).
A seam is a path from the top row to the bottom row, advancing one row at a
time and moving by at most one pixel horizontally (8-connected vertical path).

Cost of a cell::

    cost(y, x) = mean|channel_L(x) - channel_R(x' - dx)|
               + grad_weight * (|grad L| + |grad R|)

* the first term is the *colour-difference* energy: placing the seam where the
  two photos disagree is cheap if they agree there;
* the second term is the *edge-avoidance* energy: high-gradient pixels belong
  to high-contrast content (edges of a subject) and receive a large penalty,
  so the seam is routed around the subject.

The minimum-cost path is found by dynamic programming::

    M(0, x) = cost(0, x)
    M(y, x) = cost(y, x) + min(M(y-1, x-1), M(y-1, x), M(y-1, x+1))

then backtracking from ``argmin M(H-1, :)``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .image import Image

INF = float("inf")


@dataclass
class StitchResult:
    status: str                       # "seam" | "fallback_blend" | "rejected"
    image: Optional[Image] = None
    overlap: int = 0
    offset_x: int = 0
    seam: List[int] = field(default_factory=list)   # seam x per row (overlap-local)
    seam_cost_total: float = 0.0
    mean_lum_left: float = 0.0        # before compensation
    mean_lum_right: float = 0.0
    gain_applied_to_left: float = 1.0
    lum_diff_before: float = 0.0      # mean |lum_L - lum_R| in overlap, raw
    lum_diff_after: float = 0.0       # mean |lum_L' - lum_R| after compensation
    crossings_pixels: int = 0         # seam pixels sitting on high-contrast mask
    crossings_segments: int = 0       # contiguous runs of such pixels


def _overlap_luminance(left: Image, right: Image, overlap: int, offset: int):
    """Return per-cell luminance grids for the overlap and the means."""
    h = left.height
    wl, wr = left.width, right.width
    sum_l = sum_r = 0.0
    grid_l = [0.0] * (overlap * h)
    grid_r = [0.0] * (overlap * h)
    for y in range(h):
        row = y * overlap
        base_l = y * wl + offset
        base_r = y * wr
        for i in range(overlap):
            rl, gl, bl = left.data[base_l + i]
            rr, gr, br = right.data[base_r + i]
            ll = 0.299 * rl + 0.587 * gl + 0.114 * bl
            lr = 0.299 * rr + 0.587 * gr + 0.114 * br
            grid_l[row + i] = ll
            grid_r[row + i] = lr
            sum_l += ll
            sum_r += lr
    n = overlap * h
    return grid_l, grid_r, sum_l / n, sum_r / n


def _gradient_magnitude(grid: List[float], w: int, h: int) -> List[float]:
    out = [0.0] * (w * h)
    for y in range(h):
        row = y * w
        for x in range(w):
            xl = grid[row + (x - 1 if x > 0 else x)]
            xr = grid[row + (x + 1 if x + 1 < w else x)]
            yu = grid[(row - w) + x] if y > 0 else grid[row + x]
            yd = grid[(row + w) + x] if y + 1 < h else grid[row + x]
            out[row + x] = abs(xr - xl) * 0.5 + abs(yd - yu) * 0.5
    return out


def _dp_seam(cost: List[float], w: int, h: int):
    """Minimum-cost vertical seam.  Returns (path, full DP table)."""
    dp = [INF] * (w * h)
    dp[:w] = cost[:w]
    for y in range(1, h):
        row = y * w
        prow = row - w
        for x in range(w):
            best = dp[prow + x]
            if x > 0 and dp[prow + x - 1] < best:
                best = dp[prow + x - 1]
            if x + 1 < w and dp[prow + x + 1] < best:
                best = dp[prow + x + 1]
            dp[row + x] = cost[row + x] + best
    path = [0] * h
    last = (h - 1) * w
    best_x = 0
    best_v = dp[last]
    for x in range(1, w):
        if dp[last + x] < best_v:
            best_v = dp[last + x]
            best_x = x
    path[h - 1] = best_x
    for y in range(h - 2, -1, -1):
        x = path[y + 1]
        bx = x
        bv = dp[y * w + x]
        if x > 0 and dp[y * w + x - 1] < bv:
            bx, bv = x - 1, dp[y * w + x - 1]
        if x + 1 < w and dp[y * w + x + 1] < bv:
            bx = x + 1
        path[y] = bx
    return path, dp


def _compose_feathered(left: Image, right: Image, offset: int,
                       seam: List[int], feather: int) -> Image:
    h, wl = left.height, left.width
    canvas_w = offset + right.width
    out: List[tuple] = [None] * (canvas_w * h)  # type: ignore
    for y in range(h):
        sx = seam[y]
        cbase = y * canvas_w
        lbase = y * wl
        rbase = y * right.width
        for cx in range(canvas_w):
            if cx < offset:
                out[cbase + cx] = left.data[lbase + cx]
            elif cx >= wl:
                out[cbase + cx] = right.data[rbase + (cx - offset)]
            else:
                lpx = left.data[lbase + cx]
                rpx = right.data[rbase + (cx - offset)]
                d = cx - offset - sx
                if feather <= 0 or d <= -feather:
                    out[cbase + cx] = lpx
                elif d >= feather:
                    out[cbase + cx] = rpx
                else:
                    t = (d + feather) / (2.0 * feather)
                    out[cbase + cx] = (
                        int(round(lpx[0] * (1 - t) + rpx[0] * t)),
                        int(round(lpx[1] * (1 - t) + rpx[1] * t)),
                        int(round(lpx[2] * (1 - t) + rpx[2] * t)),
                    )
    return Image(canvas_w, h, out)


def _linear_ramp(left: Image, right: Image, offset: int) -> Image:
    """Deterministic fallback: linear alpha ramp across the whole overlap."""
    h, wl = left.height, left.width
    overlap = wl - offset
    canvas_w = offset + right.width
    out: List[tuple] = [None] * (canvas_w * h)  # type: ignore
    for y in range(h):
        cbase, lbase, rbase = y * canvas_w, y * wl, y * right.width
        for cx in range(canvas_w):
            if cx < offset:
                out[cbase + cx] = left.data[lbase + cx]
            elif cx >= wl:
                out[cbase + cx] = right.data[rbase + (cx - offset)]
            else:
                t = (cx - offset) / max(1, overlap - 1)
                lp, rp = left.data[lbase + cx], right.data[rbase + cx - offset]
                out[cbase + cx] = (
                    int(round(lp[0] * (1 - t) + rp[0] * t)),
                    int(round(lp[1] * (1 - t) + rp[1] * t)),
                    int(round(lp[2] * (1 - t) + rp[2] * t)),
                )
    return Image(canvas_w, h, out)


def stitch(left: Image, right: Image, offset_x: int, *,
           min_overlap: int = 8,
           on_too_small: str = "blend",
           feather: int = 3,
           grad_weight: float = 4.0,
           subject_threshold: float = 48.0,
           gain_clamp: float = 2.0) -> StitchResult:
    """Stitch two same-height, horizontally overlapping images.

    ``right`` is placed ``offset_x`` pixels to the right of ``left``.
    ``on_too_small`` is ``"blend"`` (linear ramp fallback) or ``"reject"``.
    """
    if left.height != right.height:
        raise ValueError("images must have the same height")
    if not (0 < offset_x < left.width):
        raise ValueError("offset_x must leave a positive overlap")
    overlap = left.width - offset_x
    res = StitchResult(status="rejected", overlap=overlap, offset_x=offset_x)

    # ---- small-overlap fallback (deterministic) ---------------------------
    if overlap < min_overlap:
        if on_too_small == "reject":
            return res
        res.status = "fallback_blend"
        res.image = _linear_ramp(left, right, offset_x)
        gl, gr, ml, mr = _overlap_luminance(left, right, overlap, offset_x)
        res.mean_lum_left, res.mean_lum_right = ml, mr
        res.lum_diff_before = sum(abs(gl[k] - gr[k]) for k in range(len(gl))) / len(gl)
        res.lum_diff_after = res.lum_diff_before
        return res

    # ---- exposure compensation --------------------------------------------
    gl, gr, mean_l, mean_r = _overlap_luminance(left, right, overlap, offset_x)
    res.mean_lum_left, res.mean_lum_right = mean_l, mean_r
    diff_before = sum(abs(gl[k] - gr[k]) for k in range(overlap * left.height))
    res.lum_diff_before = diff_before / (overlap * left.height)
    if mean_l > 1.0:
        gain = max(1.0 / gain_clamp, min(gain_clamp, mean_r / mean_l))
    else:
        gain = 1.0
    res.gain_applied_to_left = gain
    if abs(gain - 1.0) > 1e-6:
        left = left.gain(gain)
        gl, gr, _, _ = _overlap_luminance(left, right, overlap, offset_x)
    n = overlap * left.height
    res.lum_diff_after = sum(abs(gl[k] - gr[k]) for k in range(n)) / n

    # ---- energy + DP seam --------------------------------------------------
    grad_l = _gradient_magnitude(gl, overlap, left.height)
    grad_r = _gradient_magnitude(gr, overlap, left.height)
    h = left.height
    cost = [0.0] * (overlap * h)
    edge_mask = bytearray(overlap * h)
    for k in range(overlap * h):
        rl, glc, bl = left.data[(k // overlap) * left.width + offset_x + (k % overlap)]
        rr, grc, br = right.data[(k // overlap) * right.width + (k % overlap)]
        coldiff = (abs(rl - rr) + abs(glc - grc) + abs(bl - br)) / 3.0
        edge = grad_l[k] + grad_r[k]
        cost[k] = coldiff + grad_weight * edge
        edge_mask[k] = 1 if (grad_l[k] > subject_threshold or
                             grad_r[k] > subject_threshold) else 0

    # Dilate the high-contrast mask by one pixel: a seam hugging an edge is
    # still counted as crossing the subject.
    subject = bytearray(overlap * h)
    for y in range(h):
        row = y * overlap
        for x in range(overlap):
            hit = edge_mask[row + x]
            if not hit:
                if x > 0 and edge_mask[row + x - 1]:
                    hit = 1
                elif x + 1 < overlap and edge_mask[row + x + 1]:
                    hit = 1
                elif y > 0 and edge_mask[row - overlap + x]:
                    hit = 1
                elif y + 1 < h and edge_mask[row + overlap + x]:
                    hit = 1
            subject[row + x] = hit

    seam, dp = _dp_seam(cost, overlap, h)
    res.seam = seam
    res.seam_cost_total = dp[(h - 1) * overlap + seam[-1]]

    # ---- "seam crosses the subject" statistics -----------------------------
    on = [subject[y * overlap + seam[y]] for y in range(h)]
    res.crossings_pixels = sum(on)
    res.crossings_segments = sum(
        1 for y in range(h) if on[y] and (y == 0 or not on[y - 1])
    )

    # ---- compose ------------------------------------------------------------
    res.status = "seam"
    res.image = _compose_feathered(left, right, offset_x, seam, feather)
    return res
