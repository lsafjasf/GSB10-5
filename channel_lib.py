"""图像通道分离与合并库（仅标准库）。

支持：
- 通道顺序: RGB / BGR / RGBA / BGRA / ARGB / ABGR
- 位深: 8 / 16 bit（16 bit 支持 little/big endian）
- 含 alpha 时区分 预乘(premultiplied) 与 非预乘(straight) 两种处理

数据布局：交错（interleaved）字节流， planes 为每通道独立的字节平面。
16 bit 平面的字节序与输入图像一致（由 endian 指定）。
"""
from __future__ import annotations

import array
import sys

__all__ = [
    "ChannelError",
    "SUPPORTED_ORDERS",
    "SUPPORTED_BIT_DEPTHS",
    "split",
    "merge",
    "alpha_index",
]

SUPPORTED_ORDERS = ("RGB", "BGR", "RGBA", "BGRA", "ARGB", "ABGR")
SUPPORTED_BIT_DEPTHS = (8, 16)

_NATIVE_LITTLE = sys.byteorder == "little"


class ChannelError(ValueError):
    """通道数 / 位深 / 数据长度不匹配时抛出，消息中指出具体不匹配位置。"""


def _bytes_per_sample(bit_depth: int) -> int:
    if bit_depth == 8:
        return 1
    if bit_depth == 16:
        return 2
    raise ChannelError(
        f"bit depth mismatch: expected one of {SUPPORTED_BIT_DEPTHS}, got {bit_depth}"
    )


def _check_order(order: str) -> str:
    order = order.upper()
    if order not in SUPPORTED_ORDERS:
        raise ChannelError(
            f"channel order mismatch: expected one of {SUPPORTED_ORDERS}, got {order!r}"
        )
    return order


def alpha_index(order: str) -> int | None:
    """返回 alpha 通道在交错数据中的下标；无 alpha 返回 None。"""
    order = order.upper()
    return order.index("A") if "A" in order else None


def _need_swap(endian: str) -> bool:
    if endian not in ("little", "big"):
        raise ChannelError(f"endian mismatch: expected 'little' or 'big', got {endian!r}")
    return (endian == "little") != _NATIVE_LITTLE


def _maxval(bit_depth: int) -> int:
    return (1 << bit_depth) - 1


def split(
    width: int,
    height: int,
    order: str,
    bit_depth: int,
    data: bytes,
    *,
    premultiplied: bool = False,
    endian: str = "little",
) -> list[bytes]:
    """把交错图像数据分离为每通道一个字节平面。

    premultiplied=True 且含 alpha 时，颜色平面输出为 颜色*alpha/maxval（预乘）。
    """
    order = _check_order(order)
    bps = _bytes_per_sample(bit_depth)
    n = len(order)
    expected = width * height * n * bps
    if len(data) != expected:
        raise ChannelError(
            f"data length mismatch: expected {expected} bytes "
            f"({width}x{height}, {n}ch '{order}', {bit_depth}bit), got {len(data)} bytes"
        )

    if bit_depth == 8:
        planes = [bytearray(data[c::n]) for c in range(n)]
    else:
        swap = _need_swap(endian)
        samples = array.array("H", data)
        if swap:
            samples.byteswap()  # 转为主机序便于按通道切片
        planes = []
        for c in range(n):
            plane = array.array("H", samples[c::n])
            if swap:
                plane.byteswap()  # 平面保持与输入相同的字节序
            planes.append(bytearray(plane.tobytes()))

    if premultiplied:
        a = alpha_index(order)
        if a is not None:
            _premultiply(planes, a, bit_depth, endian)

    return [bytes(p) for p in planes]


def merge(
    width: int,
    height: int,
    order: str,
    bit_depth: int,
    planes: list[bytes],
    *,
    premultiplied: bool = False,
    endian: str = "little",
) -> bytes:
    """把每通道字节平面合并为交错图像数据。

    premultiplied=True 且含 alpha 时，认为颜色平面已预乘，合并前做反预乘。
    """
    order = _check_order(order)
    bps = _bytes_per_sample(bit_depth)
    n = len(order)

    if len(planes) != n:
        got = len(planes)
        detail = []
        if got < n:
            detail.append(f"missing plane(s) for channel(s) {order[got:]}")
        else:
            detail.append(f"{got - n} extra plane(s) beyond channel '{order[-1]}'")
        raise ChannelError(
            f"channel count mismatch: order '{order}' needs {n} planes, got {got} "
            f"({'; '.join(detail)})"
        )

    expected = width * height * bps
    planes = [bytearray(p) for p in planes]
    for c, plane in enumerate(planes):
        if len(plane) != expected:
            actual_bps = len(plane) / (width * height) if width * height else 0
            raise ChannelError(
                f"bit depth / size mismatch at plane {c} (channel '{order[c]}'): "
                f"expected {expected} bytes ({width}x{height} @ {bit_depth}bit), "
                f"got {len(plane)} bytes (looks like {actual_bps:g} byte(s)/sample)"
            )

    if premultiplied:
        a = alpha_index(order)
        if a is not None:
            _unpremultiply(planes, a, bit_depth, endian)

    if bit_depth == 8:
        out = bytearray(width * height * n)
        for c, plane in enumerate(planes):
            out[c::n] = plane
        return bytes(out)

    swap = _need_swap(endian)
    out = array.array("H", bytes(width * height * n * 2))
    for c, plane in enumerate(planes):
        pa = array.array("H", plane)
        if swap:
            pa.byteswap()
        out[c::n] = pa
    if swap:
        out.byteswap()
    return out.tobytes()


# ---------------- 预乘 / 反预乘 ----------------

def _to_native(plane: bytearray, bit_depth: int, endian: str) -> array.array:
    if bit_depth == 8:
        return array.array("B", plane)
    arr = array.array("H", plane)
    if _need_swap(endian):
        arr.byteswap()
    return arr


def _from_native(arr: array.array, bit_depth: int, endian: str) -> bytearray:
    if bit_depth == 8:
        return bytearray(arr.tobytes())
    out = array.array("H", arr)
    if _need_swap(endian):
        out.byteswap()
    return bytearray(out.tobytes())


def _premultiply(planes: list[bytearray], a: int, bit_depth: int, endian: str) -> None:
    maxval = _maxval(bit_depth)
    half = maxval // 2
    alpha = _to_native(planes[a], bit_depth, endian)
    for c in range(len(planes)):
        if c == a:
            continue
        color = _to_native(planes[c], bit_depth, endian)
        planes[c] = _from_native(
            array.array(color.typecode, ((v * al + half) // maxval for v, al in zip(color, alpha))),
            bit_depth,
            endian,
        )


def _unpremultiply(planes: list[bytearray], a: int, bit_depth: int, endian: str) -> None:
    maxval = _maxval(bit_depth)
    alpha = _to_native(planes[a], bit_depth, endian)
    for c in range(len(planes)):
        if c == a:
            continue
        color = _to_native(planes[c], bit_depth, endian)
        planes[c] = _from_native(
            array.array(
                color.typecode,
                ((v * maxval + al // 2) // al if al else 0 for v, al in zip(color, alpha)),
            ),
            bit_depth,
            endian,
        )
