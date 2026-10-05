"""channelops.py — 图像通道分离 / 合并库（仅 Python 标准库）。

支持：
  * 交错（interleaved）裸像素数据；
  * 通道顺序：RGB / BGR / RGBA / BGRA / ARGB / ABGR（任意由
    {'R','G','B','A'} 组成的不重复字符串）；
  * 位深：8 bit、16 bit（16 bit 每样本 2 字节，字节序可选 big/little）；
  * 含 alpha 时直线（非预乘，straight/unassociated）与预乘
    （premultiplied/associated）两种表示的互转。

分离 / 合并只做字节搬运，不解释像素数值，因此分离再合并与原图逐字节一致。
"""

from __future__ import annotations

VALID_CHANNELS = ("R", "G", "B", "A")
SUPPORTED_BIT_DEPTHS = (8, 16)


class ChannelError(ValueError):
    """通道相关错误的基类。"""


class ChannelCountMismatch(ChannelError):
    """给定平面集合与图像通道顺序要求不一致。"""

    def __init__(self, expected_order, given, missing, extra):
        self.expected_order = expected_order
        self.given = tuple(sorted(given))
        self.missing = tuple(missing)
        self.extra = tuple(extra)
        detail = []
        if missing:
            detail.append(
                "缺少平面 %s（位于期望顺序 %r 的第 %s 位）"
                % (
                    list(missing),
                    expected_order,
                    [expected_order.index(c) for c in missing],
                )
            )
        if extra:
            detail.append("多余平面 %s" % list(extra))
        super().__init__(
            "通道数不匹配：期望顺序 %r（%d 通道），实际给定 %r（%d 个平面）；%s"
            % (
                expected_order,
                len(expected_order),
                list(expected_order if not given else self.given),
                len(self.given),
                "；".join(detail),
            )
        )


class BitDepthMismatch(ChannelError):
    """某个平面的字节数与其位深/几何尺寸要求不一致。"""

    def __init__(self, plane, plane_index, expected_bit_depth, expected_bytes,
                 actual_bytes):
        self.plane = plane
        self.plane_index = plane_index
        self.expected_bit_depth = expected_bit_depth
        self.expected_bytes = expected_bytes
        self.actual_bytes = actual_bytes
        super().__init__(
            "位深不匹配：第 %d 个平面 %r 应为 %d-bit、每像素 %d 字节，"
            "期望长度 %d 字节，实际长度 %d 字节（相差 %+d 字节）"
            % (
                plane_index,
                plane,
                expected_bit_depth,
                expected_bit_depth // 8,
                expected_bytes,
                actual_bytes,
                actual_bytes - expected_bytes,
            )
        )


def _validate_spec(channel_order, bit_depth, byte_order):
    if not isinstance(channel_order, str) or len(channel_order) == 0:
        raise ChannelError("channel_order 必须是非空字符串")
    if any(c not in VALID_CHANNELS for c in channel_order):
        raise ChannelError(
            "channel_order %r 含非法通道名，只允许 %s"
            % (channel_order, VALID_CHANNELS)
        )
    if len(set(channel_order)) != len(channel_order):
        raise ChannelError("channel_order %r 中通道名重复" % channel_order)
    if bit_depth not in SUPPORTED_BIT_DEPTHS:
        raise ChannelError(
            "不支持位深 %r，仅支持 %s"
            % (bit_depth, SUPPORTED_BIT_DEPTHS)
        )
    if byte_order not in ("big", "little"):
        raise ChannelError("byte_order 只允许 'big' / 'little'")


def expected_byte_size(width, height, channel_order, bit_depth):
    return width * height * len(channel_order) * (bit_depth // 8)


def split_channels(data, width, height, channel_order, bit_depth,
                   byte_order="big"):
    """把交错像素数据分离成 {通道名: 每通道平面字节串}。

    返回的平面中 16-bit 样本仍按 byte_order 保留 2 字节/样本。
    本函数是纯字节操作，不关心数值含义。
    """
    _validate_spec(channel_order, bit_depth, byte_order)
    if width <= 0 or height <= 0:
        raise ChannelError("width/height 必须为正整数")
    n = len(channel_order)
    bps = bit_depth // 8
    pixel_size = n * bps
    expect = width * height * pixel_size
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ChannelError("data 必须是 bytes/bytearray/memoryview")
    if len(data) != expect:
        raise ChannelError(
            "数据长度 %d 与 %dx%d/%s/%dbit 应有的 %d 字节不符（相差 %+d）"
            % (len(data), width, height, channel_order, bit_depth,
               expect, len(data) - expect)
        )

    view = bytes(data)
    pixels = width * height
    planes = {}
    for ci, name in enumerate(channel_order):
        offset = ci * bps
        if bps == 1:
            planes[name] = view[offset::pixel_size]
        else:
            plane = bytearray(pixels * bps)
            for k in range(bps):
                plane[k::bps] = view[offset + k::pixel_size]
            planes[name] = bytes(plane)
    return planes


def merge_channels(planes, width, height, channel_order, bit_depth,
                   byte_order="big"):
    """按 channel_order 把平面字典合并回交错字节数据。

    平面缺失/多余 -> ChannelCountMismatch（指出具体位置）；
    平面长度与位深不符 -> BitDepthMismatch（指出是哪个平面、第几位）。
    """
    _validate_spec(channel_order, bit_depth, byte_order)
    if width <= 0 or height <= 0:
        raise ChannelError("width/height 必须为正整数")
    n = len(channel_order)
    bps = bit_depth // 8
    pixel_size = n * bps
    pixels = width * height

    given = set(planes.keys())
    expected = set(channel_order)
    if given != expected:
        missing = [c for c in channel_order if c not in given]
        extra = sorted(given - expected)
        raise ChannelCountMismatch(channel_order, given, missing, extra)

    expect_plane_bytes = pixels * bps
    for ci, name in enumerate(channel_order):
        plane = planes[name]
        if not isinstance(plane, (bytes, bytearray, memoryview)):
            raise ChannelError("平面 %r 必须是字节串" % name)
        if len(plane) != expect_plane_bytes:
            raise BitDepthMismatch(
                name, ci, bit_depth, expect_plane_bytes, len(plane)
            )

    out = bytearray(pixels * pixel_size)
    for ci, name in enumerate(channel_order):
        offset = ci * bps
        view = bytes(planes[name])
        if bps == 1:
            out[offset::pixel_size] = view
        else:
            for k in range(bps):
                out[offset + k::pixel_size] = view[k::bps]
    return bytes(out)


# ---------------------------------------------------------------------------
# 预乘 / 非预乘 alpha
# ---------------------------------------------------------------------------

def _sample_iter(plane_bytes, bps, byte_order):
    fmt = "big" if byte_order == "big" else "little"
    for i in range(0, len(plane_bytes), bps):
        yield int.from_bytes(plane_bytes[i:i + bps], fmt)


def _samples_to_bytes(values, bps, byte_order):
    fmt = "big" if byte_order == "big" else "little"
    out = bytearray(len(values) * bps)
    for i, v in enumerate(values):
        out[i * bps:(i + 1) * bps] = int(v).to_bytes(bps, fmt)
    return bytes(out)


def convert_premultiplied(data, width, height, channel_order, bit_depth,
                          byte_order="big", to_premultiplied=True):
    """在直线（非预乘）与预乘 alpha 之间转换。

    to_premultiplied=True : straight -> premultiplied，颜色通道乘 a/max；
    to_premultiplied=False: premultiplied -> straight，颜色通道除以 a/max；
    alpha=0 的像素，逆变换时颜色置 0（约定黑色），并在 alpha_stats 中计数。
    """
    _validate_spec(channel_order, bit_depth, byte_order)
    if "A" not in channel_order:
        raise ChannelError(
            "通道顺序 %r 不含 alpha，无法做预乘处理" % channel_order
        )
    planes = split_channels(data, width, height, channel_order,
                            bit_depth, byte_order)
    bps = bit_depth // 8
    maxv = (1 << bit_depth) - 1
    alpha = list(_sample_iter(planes["A"], bps, byte_order))
    zero_alpha = sum(1 for a in alpha if a == 0)

    result = {}
    for name, plane in planes.items():
        if name == "A":
            result[name] = plane
            continue
        vals = list(_sample_iter(plane, bps, byte_order))
        if to_premultiplied:
            new_vals = [(c * a + maxv // 2) // maxv
                        for c, a in zip(vals, alpha)]
        else:
            new_vals = []
            for c, a in zip(vals, alpha):
                if a == 0:
                    new_vals.append(0)
                else:
                    new_vals.append(
                        min(maxv, (c * maxv + a // 2) // a)
                    )
        result[name] = _samples_to_bytes(new_vals, bps, byte_order)

    out = merge_channels(result, width, height, channel_order,
                         bit_depth, byte_order)
    stats = {"zero_alpha_pixels": zero_alpha, "pixels": len(alpha)}
    return out, stats
