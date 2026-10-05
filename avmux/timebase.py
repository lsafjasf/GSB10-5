"""时间基换算。

约定（与 FFmpeg 一致）：时间基 ``TimeBase(num, den)`` 表示 **1 个 tick = num/den 秒**。
例如 ``TimeBase(1, 48000)`` 表示 1 tick = 1/48000 秒（48 kHz 音频）。

精确换算公式（有理数，无误差）::

    ticks_dst = ticks_src * (num_src * den_dst) / (den_src * num_dst)

整数 tick 必须取整，本模块提供两种方式：

* ``ROUND_DOWN``    —— 向下取整（向负无穷，``math.floor``）；
* ``ROUND_NEAREST`` —— 四舍五入，半数远离 0（非负数即“半数向上” half-up），
  实现为 ``floor(x + 1/2)``（x>=0）。

所有跨轨迹比较一律先换算到**公共时间基** ``TimeBase(1, 1_000_000)``（1 tick = 1 us），
换算采用 ``ROUND_NEAREST``，同时保留 ``Fraction`` 精确值用于展示取整误差。
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import math

ROUND_DOWN = "down"
ROUND_NEAREST = "nearest"

MICROSECOND_TIME_BASE = None  # 在 TimeBase 定义之后赋值


def _round_exact(value: Fraction, rounding: str) -> int:
    if rounding == ROUND_DOWN:
        return math.floor(value)
    if rounding == ROUND_NEAREST:
        # 半数远离 0：非负数 half-up，负数 half-down（对称）
        if value >= 0:
            return math.floor(value + Fraction(1, 2))
        return math.ceil(value - Fraction(1, 2))
    raise ValueError(f"unknown rounding mode: {rounding!r}")


@dataclass(frozen=True)
class TimeBase:
    """1 tick = num/den 秒。num、den 必须为正整数。"""

    num: int
    den: int

    def __post_init__(self) -> None:
        if not isinstance(self.num, int) or not isinstance(self.den, int):
            raise TypeError("time_base num/den must be int")
        if self.num <= 0 or self.den <= 0:
            raise ValueError("time_base num/den must be positive")

    @property
    def seconds_per_tick(self) -> Fraction:
        return Fraction(self.num, self.den)

    def ticks_to_seconds(self, ticks: int) -> Fraction:
        """tick -> 秒（精确分数）。"""
        return Fraction(ticks) * self.seconds_per_tick

    def ticks_to_us(self, ticks: int, rounding: str = ROUND_NEAREST) -> int:
        """tick -> 微秒整数，按指定方式取整。"""
        exact = self.ticks_to_seconds(ticks) * 1_000_000
        return _round_exact(exact, rounding)

    def ticks_to_us_exact(self, ticks: int) -> Fraction:
        """tick -> 微秒（精确分数，可能非整数）。"""
        return self.ticks_to_seconds(ticks) * 1_000_000

    def __str__(self) -> str:
        return f"{self.num}/{self.den}s"


MICROSECOND_TIME_BASE = TimeBase(1, 1_000_000)


def convert_exact(value: int, src: TimeBase, dst: TimeBase) -> Fraction:
    """精确换算（Fraction），不丢精度。"""
    return Fraction(value) * src.num * dst.den / (src.den * dst.num)


def convert_ticks(
    value: int,
    src: TimeBase,
    dst: TimeBase,
    rounding: str = ROUND_NEAREST,
) -> int:
    """把 src 时间基下的整数 tick 换算为 dst 时间基下的整数 tick。

    >>> tb_a = TimeBase(1, 48000)   # 音频 1 tick = 1/48000 s
    >>> tb_u = TimeBase(1, 1_000_000)
    >>> convert_ticks(1, tb_a, tb_u)            # 1000000/48000 = 20.8333 -> 21
    21
    >>> convert_ticks(1, tb_a, tb_u, ROUND_DOWN)
    20
    """
    return _round_exact(convert_exact(value, src, dst), rounding)
