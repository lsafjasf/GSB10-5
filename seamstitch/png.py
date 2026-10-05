"""Minimal PNG writer (truecolour RGB, no interlace).  Standard library only."""

import struct
import zlib

from .image import Image


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))


def save_png(img: Image, path: str) -> None:
    raw = bytearray()
    w = img.width
    for y in range(img.height):
        raw.append(0)  # filter type 0
        base = y * w
        for x in range(w):
            raw.extend(img.data[base + x])
    ihdr = struct.pack(">IIBBBBB", img.width, img.height, 8, 2, 0, 0, 0)
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        fh.write(_chunk(b"IHDR", ihdr))
        fh.write(_chunk(b"IDAT", zlib.compress(bytes(raw), 6)))
        fh.write(_chunk(b"IEND", b""))
