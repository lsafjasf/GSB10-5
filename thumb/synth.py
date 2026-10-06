"""Synthetic images used by the experiments, tests and benchmark.

Scenes are analytic functions f(x, y) in [0,1]^2 -> (R,G,B), so the
"ground truth" thumbnail is well defined: render the same scene at the
output resolution with NxN supersampling (box integration). That gives
an objective reference that contains the true post-downsample
frequencies, which a sharpen-vs-resize comparison needs.
"""

import math

from .image import Image


def _clamp(v):
    return 0 if v < 0 else (255 if v > 255 else int(v))


def scene_texture(u, v):
    """Fine texture: sinusoidal chirp + diagonal weave."""
    chirp = 128 + 90 * math.sin(2 * math.pi * (2.5 * u + 26.0 * u * u))
    weave = 34 * math.sin(2 * math.pi * 46.0 * (u + v))
    shade = 18 * math.cos(2 * math.pi * 4.0 * v)
    yv = chirp + weave + shade
    return (_clamp(yv), _clamp(yv - 14 + 10 * math.sin(2 * math.pi * 3 * u)),
            _clamp(yv + 12))


def scene_photo(u, v):
    """Photo-like smooth scene: gaussian blobs, gradient, soft sky."""
    blobs = 0.0
    for cx, cy, s, amp in (
        (0.28, 0.32, 0.05, 110), (0.68, 0.25, 0.09, -90),
        (0.55, 0.72, 0.12, 130), (0.18, 0.8, 0.07, 70),
        (0.85, 0.62, 0.06, -60),
    ):
        dx, dy = u - cx, v - cy
        blobs += amp * math.exp(-(dx * dx + dy * dy) / (2 * s * s))
    grad = 200 - 70 * v
    r = grad + blobs
    g = grad + 0.6 * blobs + 12 * math.sin(2 * math.pi * 2 * u)
    b = grad + 0.25 * blobs + 20 + 10 * math.cos(2 * math.pi * 1.5 * (u + v))
    return (_clamp(r), _clamp(g), _clamp(b))


def scene_edges(u, v):
    """Graphics-like scene with hard edges and thin lines."""
    base = (235, 235, 230)
    rects = (
        (0.15, 0.15, 0.35, 0.5, (40, 70, 160)),
        (0.45, 0.3, 0.8, 0.7, (200, 60, 50)),
        (0.05, 0.65, 0.55, 0.95, (50, 150, 80)),
    )
    col = base
    for x0, y0, x1, y1, c in rects:
        if x0 <= u <= x1 and y0 <= v <= y1:
            col = c
    # thin lines, some thinner than an output pixel -> alias stress test
    if abs((u * 9.0) % 1.0 - 0.0) < 0.012:
        col = (10, 10, 10)
    if abs(v - 0.22) < 0.003:
        col = (255, 200, 0)
    return col


SCENES = {"texture": scene_texture, "photo": scene_photo,
          "edges": scene_edges}


def render(scene, width, height, ss=1):
    """Render an analytic scene to an Image, optionally NxN supersampled."""
    fn = scene if callable(scene) else SCENES[scene]
    data = bytearray(width * height * 3)
    k = 0
    inv_w = 1.0 / width
    inv_h = 1.0 / height
    n = float(ss * ss)
    for y in range(height):
        for x in range(width):
            sr = sg = sb = 0.0
            for sy in range(ss):
                v = (y + (sy + 0.5) / ss) * inv_h
                for sx in range(ss):
                    u = (x + (sx + 0.5) / ss) * inv_w
                    r, g, b = fn(u, v)
                    sr += r
                    sg += g
                    sb += b
            data[k] = _clamp(sr / n)
            data[k + 1] = _clamp(sg / n)
            data[k + 2] = _clamp(sb / n)
            k += 3
    return Image(width, height, data)


# ---- fast procedural bank for the 10k timing run -------------------------
# Rendering analytic scenes per pixel is too slow for 10k images, so a
# bank of 256x256 tiles is built once and benchmark images are tiled
# crops of it (plus noise, which is a C-level random-bytes call).

_BANK_SIZE = 256
_bank = {}


def _build_bank():
    _bank["texture"] = render(scene_texture, _BANK_SIZE, _BANK_SIZE, ss=1)
    _bank["photo"] = render(scene_photo, _BANK_SIZE, _BANK_SIZE, ss=1)
    _bank["edges"] = render(scene_edges, _BANK_SIZE, _BANK_SIZE, ss=1)
    noise = Image(_BANK_SIZE, _BANK_SIZE)
    import random
    rng = random.Random(1234)
    noise.data = bytearray(rng.getrandbits(8) for _ in range(len(noise.data)))
    _bank["noise"] = noise


def tile_image(kind, width, height, seed):
    """Deterministically produce a w x h crop of a pre-rendered tile bank."""
    if not _bank:
        _build_bank()
    import random
    rng = random.Random(seed)
    src = _bank[kind]
    off_x = rng.randrange(_BANK_SIZE)
    off_y = rng.randrange(_BANK_SIZE)
    swap = rng.random() < 0.5
    data = bytearray(width * height * 3)
    out_pos = 0
    for y in range(height):
        sy = (off_y + y) % _BANK_SIZE
        row_src = src.data[sy * _BANK_SIZE * 3:(sy + 1) * _BANK_SIZE * 3]
        for x in range(width):
            sx = (off_x + x) % _BANK_SIZE
            p = sx * 3
            if swap:
                data[out_pos] = row_src[p + 2]
                data[out_pos + 1] = row_src[p + 1]
                data[out_pos + 2] = row_src[p]
            else:
                data[out_pos] = row_src[p]
                data[out_pos + 1] = row_src[p + 1]
                data[out_pos + 2] = row_src[p + 2]
            out_pos += 3
    return Image(width, height, data)
