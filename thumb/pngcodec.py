"""Minimal PNG codec on top of the stdlib (zlib + struct).

Scope: 8-bit, non-interlaced, color types 0 (grayscale), 2 (RGB),
6 (RGBA). Good enough to feed the thumbnail pipeline real files and to
write results to disk. It is deliberately compact, not a full libpng.
"""

import struct
import zlib

_SIG = b"\x89PNG\r\n\x1a\n"


def _chunk(typ, payload):
    return (
        struct.pack(">I", len(payload))
        + typ
        + payload
        + struct.pack(">I", zlib.crc32(typ + payload) & 0xFFFFFFFF)
    )


def encode_png(image, level=6):
    """Encode an Image as RGB PNG bytes (color type 2, filter 0)."""
    width, height = image.width, image.height
    stride = width * 3
    raw = bytearray()
    extend = raw.extend
    d = image.data
    for y in range(height):
        raw.append(0)  # filter type None
        extend(d[y * stride:(y + 1) * stride])
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        _SIG
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(bytes(raw), level))
        + _chunk(b"IEND", b"")
    )


def write_png(path, image, level=6):
    with open(path, "wb") as fh:
        fh.write(encode_png(image, level))


def decode_png(buf):
    from .image import Image

    if buf[:8] != _SIG:
        raise ValueError("not a PNG file")
    pos = 8
    width = height = bit_depth = color_type = interlace = None
    idat = bytearray()
    while pos < len(buf):
        (length,) = struct.unpack(">I", buf[pos:pos + 4])
        typ = buf[pos + 4:pos + 8]
        payload = buf[pos + 8:pos + 8 + length]
        pos += 12 + length
        if typ == b"IHDR":
            (
                width,
                height,
                bit_depth,
                color_type,
                _comp,
                _filt,
                interlace,
            ) = struct.unpack(">IIBBBBB", payload)
        elif typ == b"IDAT":
            idat += payload
        elif typ == b"IEND":
            break
    if interlace != 0:
        raise ValueError("interlaced PNG is not supported")
    if bit_depth != 8:
        raise ValueError("only 8-bit PNG is supported")
    if color_type not in (0, 2, 6):
        raise ValueError("only grayscale/RGB/RGBA color types are supported")

    channels = {0: 1, 2: 3, 6: 4}[color_type]
    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    out = bytearray(width * height * 3)
    prev = bytearray(stride)
    line = bytearray(stride)
    o = 0
    for y in range(height):
        ftype = raw[o]
        scan = raw[o + 1:o + 1 + stride]
        o += 1 + stride
        if ftype == 0:
            line[:] = scan
        elif ftype == 1:  # Sub
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                line[i] = (scan[i] + a) & 255
        elif ftype == 2:  # Up
            for i in range(stride):
                line[i] = (scan[i] + prev[i]) & 255
        elif ftype == 3:  # Average
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                line[i] = (scan[i] + ((a + prev[i]) >> 1)) & 255
        elif ftype == 4:  # Paeth
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                p = a + b - c
                pa = abs(p - a)
                pb = abs(p - b)
                pc = abs(p - c)
                if pa <= pb and pa <= pc:
                    pr = a
                elif pb <= pc:
                    pr = b
                else:
                    pr = c
                line[i] = (scan[i] + pr) & 255
        else:
            raise ValueError("unknown PNG filter type %d" % ftype)
        base = y * width * 3
        if color_type == 0:
            for x in range(width):
                v = line[x]
                p = base + x * 3
                out[p] = out[p + 1] = out[p + 2] = v
        elif color_type == 2:
            out[base:base + width * 3] = line
        else:  # RGBA -> RGB, alpha is applied over white
            for x in range(width):
                s = x * 4
                p = base + x * 3
                al = line[s + 3]
                if al == 255:
                    out[p] = line[s]
                    out[p + 1] = line[s + 1]
                    out[p + 2] = line[s + 2]
                else:
                    ia = 255 - al
                    out[p] = (line[s] * al + 255 * ia) // 255
                    out[p + 1] = (line[s + 1] * al + 255 * ia) // 255
                    out[p + 2] = (line[s + 2] * al + 255 * ia) // 255
        prev[:] = line
    return Image(width, height, out)


def read_png(path):
    with open(path, "rb") as fh:
        return decode_png(fh.read())
