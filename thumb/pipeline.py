"""Thumbnail pipeline and the deterministic sizing policy.

Sizing policy (fit inside a box, never distort):

1. scale = min(box_w / w, box_h / h); upscaling is off by default.
2. dst = max(1, round(size * scale)) on each axis.
3. Anti-"single line of pixels" rule: if the resulting short side is
   below ``min_side`` (default 8), re-scale so the short side equals
   ``min_side``, even if the long side then overflows the box - the box
   is treated as a desired bound, not a hard crop window.
   The long side is capped at ``hard_max`` (default 8192); if that cap
   binds, the short side may fall below min_side but is never 0.
4. A genuinely tiny request (both box sides < min_side, e.g. 1x1) is
   honored literally - that is an explicit tiny thumbnail, not a
   degenerate aspect-ratio outcome.

The aspect ratio is never changed: dst_w / dst_h equals w / h up to
integer rounding, so images are never stretched into a line.
"""

from .resize import resize, unsharp_mask


class Policy:
    def __init__(self, min_side=8, hard_max=8192, allow_upscale=False):
        self.min_side = min_side
        self.hard_max = hard_max
        self.allow_upscale = allow_upscale


def fit_size(w, h, box_w, box_h, policy=None):
    """Return deterministic (dst_w, dst_h) for source w x h."""
    p = policy or Policy()
    if w < 1 or h < 1 or box_w < 1 or box_h < 1:
        raise ValueError("sizes must be >= 1")
    scale = min(box_w / w, box_h / h)
    if not p.allow_upscale:
        scale = min(scale, 1.0)
    dw = max(1, int(round(w * scale)))
    dh = max(1, int(round(h * scale)))

    short, long_ = (dw, dh) if dw <= dh else (dh, dw)
    if short < p.min_side and max(box_w, box_h) >= p.min_side:
        aspect = max(w, h) / min(w, h)
        short = p.min_side
        long_ = max(short, int(round(aspect * short)))
        if long_ > p.hard_max:
            long_ = p.hard_max
            short = max(1, int(round(long_ / aspect)))
        if dw <= dh:
            dw, dh = short, long_
        else:
            dw, dh = long_, short
    return dw, dh


def make_thumbnail(image, box_w, box_h, policy=None, kernel="triangle",
                   sharpen="after", amount=0.6, threshold=0):
    """Build a thumbnail.

    sharpen:
      "after"  - low-pass downscale, then unsharp mask on the small image
      "before" - unsharp mask on the source, then low-pass downscale
                 (the risky order, kept for comparison)
      None     - low-pass downscale only
    """
    dw, dh = fit_size(image.width, image.height, box_w, box_h, policy)
    if sharpen == "before":
        sharpened = unsharp_mask(image, amount=amount, threshold=threshold)
        return resize(sharpened, dw, dh, kernel=kernel)
    out = resize(image, dw, dh, kernel=kernel)
    if sharpen == "after":
        out = unsharp_mask(out, amount=amount, threshold=threshold)
    elif sharpen is not None and sharpen != "none":
        raise ValueError("sharpen must be 'after', 'before' or None")
    return out
