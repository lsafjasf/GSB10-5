"""最小图像容器与 PGM/PPM 读写（P5/P6，二进制）。

只支持标准库环境下最易解析的无损格式:
- P5: 8/16 位灰度 PGM
- P6: 8/16 位真彩色 PPM
测试图全部由自测代码合成，无需任何第三方图像库。
"""

from dataclasses import dataclass


@dataclass
class Image:
    width: int
    height: int
    channels: int          # 1 或 3
    data: bytearray        # 每通道 8 位（读入时 16 位会被截到高 8 位）

    def luma(self):
        """返回 0..255 灰度 bytearray（BT.601 整数权重）。"""
        w, h, c, data = self.width, self.height, self.channels, self.data
        if c == 1:
            return bytearray(data)
        out = bytearray(w * h)
        i = 0
        for p in range(w * h):
            r = data[i]
            g = data[i + 1]
            b = data[i + 2]
            out[p] = (77 * r + 150 * g + 29 * b + 128) >> 8
            i += 3
        return out


def _read_tokens(buf, pos, count):
    """读取 PNM 头里的 token（自动跳过空白与 # 注释）。"""
    tokens = []
    n = len(buf)
    while len(tokens) < count:
        while pos < n and buf[pos] in b" \t\r\n":
            pos += 1
        if pos < n and buf[pos] == ord("#"):
            while pos < n and buf[pos] not in b"\r\n":
                pos += 1
            continue
        start = pos
        while pos < n and buf[pos] not in b" \t\r\n#":
            pos += 1
        if start == pos:
            raise ValueError("损坏的 PNM 头部")
        tokens.append(buf[start:pos])
    return tokens, pos


def load(path):
    with open(path, "rb") as f:
        buf = f.read()
    if len(buf) < 2:
        raise ValueError("文件过小，不是 PNM 图像")
    magic = buf[:2]
    if magic == b"P5":
        channels = 1
    elif magic == b"P6":
        channels = 3
    else:
        raise ValueError("仅支持 P5(PGM)/P6(PPM)，收到 magic=%r" % magic)
    (tok_w, tok_h, tok_m), pos = _read_tokens(buf, 2, 3)
    width, height, maxval = int(tok_w), int(tok_h), int(tok_m)
    if width <= 0 or height <= 0 or not (1 <= maxval <= 65535):
        raise ValueError("非法的 PNM 尺寸/位深")
    if pos >= len(buf) or buf[pos] not in b" \t\r\n":
        raise ValueError("PNM 头后缺少单个空白符")
    pos += 1
    bytes_per_sample = 2 if maxval > 255 else 1
    need = width * height * channels * bytes_per_sample
    raw = buf[pos:pos + need]
    if len(raw) < need:
        raise ValueError("像素数据不足: 需要 %d，实际 %d" % (need, len(raw)))
    data = bytearray(width * height * channels)
    if bytes_per_sample == 1:
        data[:] = raw
    else:
        # 16 位大端样本折算到 8 位
        k = 0
        step = channels
        for i in range(0, need, 2):
            v = (raw[i] << 8) | raw[i + 1]
            data[k] = (v * 255) // maxval
            k += 1
        del step
    return Image(width, height, channels, data)


def save(path, image):
    image.data = bytearray(image.data)
    hdr = "P%d\n%d %d\n255\n" % (
        5 if image.channels == 1 else 6, image.width, image.height)
    with open(path, "wb") as f:
        f.write(hdr.encode("ascii"))
        f.write(image.data)


def new(width, height, channels, fill=(0, 0, 0)):
    if channels == 1:
        data = bytearray([fill[0]]) * (width * height)
    else:
        px = bytes(fill[:3])
        data = bytearray(px * (width * height))
    return Image(width, height, channels, data)


def fill_rect(image, x0, y0, x1, y1, color):
    """在 [x0,x1)×[y0,y1) 矩形内填色（越界自动裁剪）。"""
    w, h, c = image.width, image.height, image.channels
    x0 = max(0, min(x0, w)); x1 = max(0, min(x1, w))
    y0 = max(0, min(y0, h)); y1 = max(0, min(y1, h))
    if x1 <= x0 or y1 <= y0:
        return
    px = bytes(color[:c])
    row = px * (x1 - x0)
    for y in range(y0, y1):
        start = (y * w + x0) * c
        image.data[start:start + (x1 - x0) * c] = row
