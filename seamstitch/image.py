"""Minimal RGB image type and binary PPM (P6/P3) I/O. Standard library only."""

from __future__ import annotations

from typing import Callable, Iterable, List, Tuple

RGB = Tuple[int, int, int]


class Image:
    __slots__ = ("width", "height", "data")

    def __init__(self, width: int, height: int, data: List[RGB]):
        if width <= 0 or height <= 0:
            raise ValueError("image dimensions must be positive")
        if len(data) != width * height:
            raise ValueError("pixel buffer size mismatch")
        self.width = width
        self.height = height
        self.data = data

    @classmethod
    def from_fn(cls, width: int, height: int, fn: Callable[[int, int], RGB]) -> "Image":
        return cls(width, height, [fn(x, y) for y in range(height) for x in range(width)])

    @classmethod
    def solid(cls, width: int, height: int, color: RGB) -> "Image":
        return cls(width, height, [color] * (width * height))

    def get(self, x: int, y: int) -> RGB:
        return self.data[y * self.width + x]

    def luminance(self, x: int, y: int) -> float:
        r, g, b = self.get(x, y)
        return 0.299 * r + 0.587 * g + 0.114 * b

    def copy(self) -> "Image":
        return Image(self.width, self.height, list(self.data))

    def crop(self, x0: int, y0: int, w: int, h: int) -> "Image":
        if not (0 <= x0 and x0 + w <= self.width and 0 <= y0 and y0 + h <= self.height):
            raise ValueError("crop out of bounds")
        src, sw = self.data, self.width
        out: List[RGB] = []
        for y in range(y0, y0 + h):
            base = y * sw + x0
            out.extend(src[base:base + w])
        return Image(w, h, out)

    def gain(self, factor: float) -> "Image":
        """Multiply every channel by ``factor`` (exposure compensation)."""
        def scale(px: RGB) -> RGB:
            r, g, b = px
            return (
                min(255, int(round(r * factor))),
                min(255, int(round(g * factor))),
                min(255, int(round(b * factor))),
            )
        return Image(self.width, self.height, [scale(px) for px in self.data])

    def save_ppm(self, path: str) -> None:
        with open(path, "wb") as fh:
            fh.write(f"P6\n{self.width} {self.height}\n255\n".encode("ascii"))
            fh.write(bytes(v for px in self.data for v in px))

    @classmethod
    def load_ppm(cls, path: str) -> "Image":
        with open(path, "rb") as fh:
            buf = fh.read()
        tokens: List[bytes] = []
        i, n = 0, len(buf)

        def read_token() -> bytes:
            nonlocal i
            while i < n:
                if buf[i] in b" \t\r\n":
                    i += 1
                elif buf[i] == ord("#"):
                    while i < n and buf[i] not in b"\r\n":
                        i += 1
                else:
                    j = i
                    while i < n and buf[i] not in b" \t\r\n":
                        i += 1
                    return buf[j:i]
            raise ValueError("unexpected end of PPM header")

        magic = read_token()
        width = int(read_token())
        height = int(read_token())
        maxval = int(read_token())
        if magic != b"P6" and magic != b"P3":
            raise ValueError(f"unsupported magic {magic!r}")
        i += 1  # single whitespace after maxval
        if magic == b"P6":
            raw = buf[i:i + width * height * 3]
            if maxval != 255 or len(raw) != width * height * 3:
                raise ValueError("unsupported or truncated P6 raster")
            data = [(raw[k], raw[k + 1], raw[k + 2]) for k in range(0, len(raw), 3)]
        else:
            while len(tokens) < width * height * 3:
                tokens.append(read_token())
            vals = [int(t) for t in tokens]
            data = [(vals[k], vals[k + 1], vals[k + 2]) for k in range(0, len(vals), 3)]
        return cls(width, height, data)
