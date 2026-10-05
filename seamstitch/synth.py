"""Deterministic synthetic scenes used by the self-tests, cases and benchmark."""

from __future__ import annotations

import math
import random
from typing import Tuple

from .image import Image


def gradient_scene(width: int, height: int) -> Image:
    """Smooth sky/ground gradient panorama (low-frequency background)."""
    def fn(x: int, y: int):
        t = y / max(1, height - 1)
        r = int(120 + 60 * (1 - t) + 20 * math.sin(x / 17.0))
        g = int(140 + 50 * (1 - t))
        b = int(190 - 60 * t)
        return (min(255, r), min(255, g), max(0, b))
    return Image.from_fn(width, height, fn)


def add_rect(img: Image, x0: int, y0: int, x1: int, y1: int, color) -> None:
    for y in range(max(0, y0), min(img.height, y1)):
        base = y * img.width
        for x in range(max(0, x0), min(img.width, x1)):
            img.data[base + x] = color


def add_disc(img: Image, cx: int, cy: int, radius: int, color) -> None:
    r2 = radius * radius
    for y in range(max(0, cy - radius), min(img.height, cy + radius + 1)):
        base = y * img.width
        for x in range(max(0, cx - radius), min(img.width, cx + radius + 1)):
            if (x - cx) ** 2 + (y - cy) ** 2 <= r2:
                img.data[base + x] = color


def add_noise(img: Image, amplitude: int, seed: int) -> None:
    rng = random.Random(seed)
    for i, (r, g, b) in enumerate(img.data):
        d = rng.randint(-amplitude, amplitude)
        img.data[i] = (min(255, max(0, r + d)),
                       min(255, max(0, g + d)),
                       min(255, max(0, b + d)))


def make_pair(scene: Image, view_w: int, offset_x: int,
              gain_right: float = 1.0) -> Tuple[Image, Image]:
    """Cut two overlapping views out of a panorama; optionally darken/brighten
    the right view to simulate exposure difference."""
    left = scene.crop(0, 0, view_w, scene.height)
    right = scene.crop(offset_x, 0, view_w, scene.height)
    if abs(gain_right - 1.0) > 1e-9:
        right = right.gain(gain_right)
    return left, right


def add_striped_bar(img: Image, x0: int, x1: int, period: int = 6) -> None:
    """Full-height vertical bar with horizontal stripes: high-contrast content
    at EVERY column, so any vertical seam through it crosses the subject."""
    for y in range(img.height):
        dark = (y // period) % 2 == 0
        color = (8, 8, 8) if dark else (245, 245, 245)
        base = y * img.width
        for x in range(max(0, x0), min(img.width, x1)):
            img.data[base + x] = color
