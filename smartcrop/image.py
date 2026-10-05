"""Image I/O and container. Standard library only.

Supported input: binary PPM (P6), PGM (P5), BMP (24/32-bit uncompressed).
Output: PPM P6 (color) / PGM P5 (gray).
"""
from __future__ import annotations

import struct


class Image:
    __slots__ = ("width", "height", "gray", "rgb")

    def __init__(self, width, height, gray, rgb=None):
        self.width = width
        self.height = height
        self.gray = gray          # bytes, len == width*height
        self.rgb = rgb            # bytes, len == width*height*3 or None


def _read_token(f):
    token = bytearray()
    while True:
        ch = f.read(1)
        if not ch:
            break
        if ch == b"#":
            while ch not in (b"\n", b""):
                ch = f.read(1)
            if token:
                break
            continue
        if ch in b" \t\r\n":
            if token:
                break
            continue
        token += ch
    return bytes(token)


def _load_pnm(f, magic):
    width = int(_read_token(f))
    height = int(_read_token(f))
    maxval = int(_read_token(f))
    if maxval != 255:
        raise ValueError("only 8-bit PNM supported, maxval=%d" % maxval)
    if magic == b"P6":
        rgb = f.read(width * height * 3)
        if len(rgb) != width * height * 3:
            raise ValueError("truncated P6 data")
        gray = bytearray(width * height)
        for i in range(width * height):
            r = rgb[3 * i]
            g = rgb[3 * i + 1]
            b = rgb[3 * i + 2]
            gray[i] = (77 * r + 150 * g + 29 * b) >> 8
        return Image(width, height, bytes(gray), rgb)
    if magic == b"P5":
        gray = f.read(width * height)
        if len(gray) != width * height:
            raise ValueError("truncated P5 data")
        return Image(width, height, gray, None)
    raise ValueError("unsupported magic %r" % magic)


def _load_bmp(f):
    header = f.read(14)
    if len(header) != 14 or header[:2] != b"BM":
        raise ValueError("not a BMP file")
    data_offset = struct.unpack_from("<I", header, 10)[0]
    dib_size = struct.unpack("<I", f.read(4))[0]
    if dib_size < 40:
        raise ValueError("unsupported BMP header size %d" % dib_size)
    rest = f.read(dib_size - 4)
    width, height_signed, planes, bpp, compression = struct.unpack_from("<iiHHI", rest, 0)
    if planes != 1 or compression != 0 or bpp not in (24, 32):
        raise ValueError("only uncompressed 24/32-bit BMP supported")
    top_down = height_signed < 0
    height = abs(height_signed)
    row_size = ((bpp * width + 31) // 32) * 4
    f.seek(data_offset)
    raw = f.read(row_size * height)
    if len(raw) != row_size * height:
        raise ValueError("truncated BMP data")
    rgb = bytearray(width * height * 3)
    gray = bytearray(width * height)
    step = bpp // 8
    for row in range(height):
        src_row = row if top_down else height - 1 - row
        base = src_row * row_size
        for x in range(width):
            o = base + x * step
            b, g, r = raw[o], raw[o + 1], raw[o + 2]
            i = row * width + x
            rgb[3 * i] = r
            rgb[3 * i + 1] = g
            rgb[3 * i + 2] = b
            gray[i] = (77 * r + 150 * g + 29 * b) >> 8
    return Image(width, height, bytes(gray), bytes(rgb))


def load_image(path):
    with open(path, "rb") as f:
        magic = f.read(2)
        if magic in (b"P6", b"P5"):
            return _load_pnm(f, magic)
        if magic == b"BM":
            f.seek(0)
            return _load_bmp(f)
    raise ValueError("unsupported image format (want P6/P5/BMP): %s" % path)


def save_ppm(image, path):
    if image.rgb is not None:
        with open(path, "wb") as f:
            f.write(b"P6\n%d %d\n255\n" % (image.width, image.height))
            f.write(image.rgb)
    else:
        with open(path, "wb") as f:
            f.write(b"P5\n%d %d\n255\n" % (image.width, image.height))
            f.write(image.gray)


def crop_image(image, box):
    x0, y0, cw, ch = box
    w = image.width
    if image.rgb is not None:
        out = bytearray(cw * ch * 3)
        for row in range(ch):
            src = ((y0 + row) * w + x0) * 3
            out[row * cw * 3:(row + 1) * cw * 3] = image.rgb[src:src + cw * 3]
        return Image(cw, ch, None, bytes(out))
    out = bytearray(cw * ch)
    for row in range(ch):
        src = (y0 + row) * w + x0
        out[row * cw:(row + 1) * cw] = image.gray[src:src + cw]
    return Image(cw, ch, bytes(out), None)
