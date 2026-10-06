"""Deterministic target-size policy.

Rules (in order, no randomness anywhere):
1. scale = min(max_w/w, max_h/h); unless allow_upscale, scale = min(scale, 1).
2. If scale == 1 the source is already small enough: it is returned as-is
   (we never upscale, and min_dim never enlarges a small source).
3. out = floor(dim * scale + 0.5) (deterministic half-up), floored at 1.
4. If either side would drop below min_dim (default 4px, so output is never
   squashed into a 1-2px "line"), rescale so the *shorter* output side is
   exactly min_dim and the longer side follows the source aspect ratio.
5. The longer side is then capped at max_long; only when the cap bites do we
   keep (min_dim, max_long) as-is -- the one documented case where the aspect
   ratio is not reproduced exactly (a 100000x1 banner cannot be both >=4px
   tall and proportionally wide without exploding memory).
6. min_dim never exceeds the requested box: fit_size(..., 1, 1) still yields
   1x1, so very small explicit targets are honored.
"""


def _rnd(x):
    return int(x + 0.5)


def fit_size(w, h, max_w, max_h, min_dim=4, max_long=8192, allow_upscale=False):
    if w < 1 or h < 1:
        raise ValueError("source must be >= 1px")
    if max_w < 1 or max_h < 1:
        raise ValueError("target box must be >= 1px")
    eff_min = max(1, min(min_dim, max_w, max_h))
    scale = min(max_w / w, max_h / h)
    if not allow_upscale and scale >= 1.0:
        return w, h
    ow = max(1, _rnd(w * scale))
    oh = max(1, _rnd(h * scale))
    if (ow < eff_min or oh < eff_min) and min_dim:
        short = w if w <= h else h
        scale = eff_min / short
        ow = max(1, _rnd(w * scale))
        oh = max(1, _rnd(h * scale))
        long_side = max(ow, oh)
        if long_side > max_long:
            shrink = max_long / long_side
            ow = max(eff_min, _rnd(ow * shrink))
            oh = max(eff_min, _rnd(oh * shrink))
    return ow, oh
