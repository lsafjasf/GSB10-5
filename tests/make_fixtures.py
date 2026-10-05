"""Deterministic synthetic test images (seeded, reproducible)."""
from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from smartcrop.image import Image, save_ppm

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _noise_row(rng, width, lo, hi):
    return bytes(rng.randint(lo, hi) for _ in range(width))


def _flat(width, height, value):
    return Image(width, height, bytes([value]) * (width * height))


def _paste_noise_rect(img, rng, x0, y0, w, h, lo, hi):
    """Overwrite a rectangle with strong texture (guaranteed edges/contrast)."""
    buf = bytearray(img.gray)
    for y in range(y0, y0 + h):
        base = y * img.width + x0
        buf[base:base + w] = _noise_row(rng, w, lo, hi)
    return Image(img.width, img.height, bytes(buf), img.rgb)


def _frame_rect(img, x0, y0, w, h, value, thickness=3):
    """Draw a bright frame (outline) -> strong edges on all 4 sides."""
    buf = bytearray(img.gray)
    W = img.width
    for t in range(thickness):
        for x in range(x0, x0 + w):
            buf[(y0 + t) * W + x] = value
            buf[(y0 + h - 1 - t) * W + x] = value
        for y in range(y0, y0 + h):
            buf[y * W + x0 + t] = value
            buf[y * W + x0 + w - 1 - t] = value
    return Image(img.width, img.height, bytes(buf), img.rgb)


def make_center_subject():
    rng = random.Random(1001)
    img = _flat(400, 300, 128)
    img = _paste_noise_rect(img, rng, 140, 90, 120, 120, 0, 255)
    img = _frame_rect(img, 140, 90, 120, 120, 255)
    return img


def make_edge_subject():
    rng = random.Random(1002)
    img = _flat(400, 300, 128)
    # subject flush against left and bottom edges
    img = _paste_noise_rect(img, rng, 0, 190, 110, 110, 0, 255)
    img = _frame_rect(img, 0, 190, 110, 110, 255)
    return img


def make_corner_subject():
    rng = random.Random(1003)
    img = _flat(400, 300, 128)
    img = _paste_noise_rect(img, rng, 320, 0, 80, 80, 0, 255)
    img = _frame_rect(img, 320, 0, 80, 80, 255)
    return img


def make_no_subject_noise():
    rng = random.Random(1004)
    w, h = 400, 300
    rows = [_noise_row(rng, w, 0, 255) for _ in range(h)]
    return Image(w, h, b"".join(rows))


def make_no_subject_flat():
    return _flat(400, 300, 200)


def make_tiny_subject():
    rng = random.Random(1005)
    img = _flat(400, 300, 128)
    img = _paste_noise_rect(img, rng, 250, 120, 10, 10, 0, 255)
    return img


def make_wide_subject():
    rng = random.Random(1006)
    img = _flat(400, 300, 128)
    img = _paste_noise_rect(img, rng, 20, 120, 360, 60, 0, 255)
    img = _frame_rect(img, 20, 120, 360, 60, 255)
    return img


def make_tall_subject():
    rng = random.Random(1007)
    img = _flat(400, 300, 128)
    img = _paste_noise_rect(img, rng, 180, 10, 60, 280, 0, 255)
    img = _frame_rect(img, 180, 10, 60, 280, 255)
    return img


BUILDERS = {
    "center_subject": make_center_subject,
    "edge_subject": make_edge_subject,
    "corner_subject": make_corner_subject,
    "no_subject_noise": make_no_subject_noise,
    "no_subject_flat": make_no_subject_flat,
    "tiny_subject": make_tiny_subject,
    "wide_subject": make_wide_subject,
    "tall_subject": make_tall_subject,
}


def build_all():
    os.makedirs(FIXTURE_DIR, exist_ok=True)
    paths = {}
    for name, builder in BUILDERS.items():
        img = builder()
        path = os.path.join(FIXTURE_DIR, name + ".ppm")
        save_ppm(img, path)
        paths[name] = path
    return paths


if __name__ == "__main__":
    for name, path in build_all().items():
        print(name, "->", path)
