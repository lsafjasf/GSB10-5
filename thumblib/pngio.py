"""Minimal PNG codec (8-bit, non-interlaced) plus PPM, stdlib only.

Decode supports color types 0 (gray), 2 (RGB), 3 (palette), 4 (gray+alpha),
6 (RGBA) at bit depth 8, all five scanline filters.
Encode writes color type 0/2/4/6 with filter 0.
"""

import struct
import zlib

from .image import Img, MODES

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_COLOR_FOR_MODE = {"L": 0, "RGB": 2, "LA": 4, "RGBA": 6}
_MODE_FOR_COLOR = {0: "L", 2: "RGB", 3: "RGB", 4: "LA", 6: "RGBA"}


def encode_png(img, level=6):
    color = _COLOR_FOR_MODE[img.mode]
    nch = img.nch
    w, h = img.width, img.height
    raw = bytearray()
    stride = w * nch
    planes = img.channels
    for y in range(h):
        raw.append(0)
        base = y * w
        if nch == 1:
            raw += planes[0][base:base + w]
        else:
            row = bytearray(stride)
            for c in range(nch):
                row[c::nch] = planes[c][base:base + w]
            raw += row
    ihdr = struct.pack(">IIBBBBB", w, h, 8, color, 0, 0, 0)
    out = bytearray(_PNG_SIG)
    for tag, payload in ((b"IHDR", ihdr),
                         (b"IDAT", zlib.compress(bytes(raw), level)),
                         (b"IEND", b"")):
        out += struct.pack(">I", len(payload)) + tag + payload
        out += struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    return bytes(out)


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def decode_png(data):
    if data[:8] != _PNG_SIG:
        raise ValueError("not a PNG")
    pos = 8
    ihdr = None
    idat = bytearray()
    palette = None
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if tag == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", payload)
        elif tag == b"PLTE":
            palette = payload
        elif tag == b"IDAT":
            idat += payload
        elif tag == b"IEND":
            break
    if ihdr is None:
        raise ValueError("missing IHDR")
    w, h, depth, color, comp, filt, interlace = ihdr
    if depth != 8:
        raise ValueError("only 8-bit PNG supported, got depth %d" % depth)
    if interlace != 0:
        raise ValueError("interlaced PNG not supported")
    if color not in _MODE_FOR_COLOR:
        raise ValueError("bad color type %d" % color)
    if color == 3 and palette is None:
        raise ValueError("palette image without PLTE")
    src_nch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    stride = w * src_nch
    raw = zlib.decompress(bytes(idat))
    expected = (stride + 1) * h
    if len(raw) < expected:
        raise ValueError("truncated image data")
    recon = bytearray(stride * h)
    prev = bytearray(stride)
    off = 0
    for y in range(h):
        ftype = raw[off]
        off += 1
        line = bytearray(raw[off:off + stride])
        off += stride
        if ftype == 1:
            for i in range(src_nch, stride):
                line[i] = (line[i] + line[i - src_nch]) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                left = line[i - src_nch] if i >= src_nch else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                a = line[i - src_nch] if i >= src_nch else 0
                c = prev[i - src_nch] if i >= src_nch else 0
                line[i] = (line[i] + _paeth(a, prev[i], c)) & 0xFF
        elif ftype != 0:
            raise ValueError("bad filter %d" % ftype)
        recon[y * stride:(y + 1) * stride] = line
        prev = line
    mode = _MODE_FOR_COLOR[color]
    nch = MODES[mode]
    planes = [bytearray(w * h) for _ in range(nch)]
    if color == 3:
        for i, idx in enumerate(recon):
            p = idx * 3
            planes[0][i] = palette[p]
            planes[1][i] = palette[p + 1]
            planes[2][i] = palette[p + 2]
    elif src_nch == nch:
        if nch == 1:
            planes[0][:] = recon
        else:
            for c in range(nch):
                planes[c][:] = recon[c::nch]
    else:
        raise ValueError("unsupported channel mapping")
    return Img(w, h, mode, planes)


def load_png(path):
    with open(path, "rb") as fh:
        return decode_png(fh.read())


def save_png(img, path, level=6):
    with open(path, "wb") as fh:
        fh.write(encode_png(img, level))


def encode_ppm(img):
    """Binary PPM (P6) / PGM (P5) for quick interop."""
    w, h = img.width, img.height
    if img.mode in ("L", "LA"):
        return b"P5\n%d %d\n255\n" % (w, h) + bytes(img.channels[0])
    n = w * h
    inter = bytearray(n * 3)
    for c in range(3):
        inter[c::3] = img.channels[c]
    return b"P6\n%d %d\n255\n" % (w, h) + bytes(inter)
