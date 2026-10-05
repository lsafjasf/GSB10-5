"""色调曲线映射库（仅标准库）。

构造方法（详见 CONSTRUCTION_NOTES.md）：
- 输入：归一化控制点 [(x, y), ...]，x、y 都在 [0, 1]，表示输入亮度占空比 -> 输出亮度占空比。
- 插值：控制点先按 x 升序排序，相邻控制点之间做分段线性插值（piecewise linear）。
- 端点：强制 (0.0 -> 0.0) 与 (1.0 -> 1.0)。缺失则补点，存在但越界/不达标则强制改写。
- 查表：在 256 级（8 位）域上，对 k=0..255 求 y(k/255)，四舍五入成 0..255，得到 256 字节 LUT；
  LUT[0]=0、LUT[255]=255 被再次强制。
- 映射：对 8 位灰度图做 bytes.translate(LUT)。

单调性策略：
- 控制点两两相邻校验（非递减 y[i-1] <= y[i]），逐条报告违例；
- 违例则用保序回归（PAVA，加权相邻违约合并，权重均为 1）修正为最近的非递减曲线，端点钉死 0/1；
- LUT 的 255 对相邻项再逐一校验；如出现反转则用累计最大值强制修正（理论上不会触发，属于防御性兜底）。
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from typing import Sequence

_EPS = 1e-12


@dataclass
class PairViolation:
    """一对相邻控制点/LUT 项的单调性违例记录。"""

    pair_index: int  # 第 pair_index 对：points[pair_index-1] -> points[pair_index]
    x0: float
    y0: float
    x1: float
    y1: float

    def describe(self) -> str:
        return (
            f"pair#{self.pair_index}: ({self.x0:.4f},{self.y0:.4f}) -> "
            f"({self.x1:.4f},{self.y1:.4f}), delta_y={self.y1 - self.y0:+.4f} < 0"
        )


@dataclass
class CurveBuildReport:
    """构造全过程的校验/修正记录。"""

    raw_points: list[tuple[float, float]]
    prepared_points: list[tuple[float, float]] = field(default_factory=list)
    final_points: list[tuple[float, float]] = field(default_factory=list)
    duplicate_x_merged: list[float] = field(default_factory=list)
    y_clamped: list[tuple[int, float, float]] = field(default_factory=list)
    endpoint_actions: list[str] = field(default_factory=list)
    cp_violations_before: list[PairViolation] = field(default_factory=list)
    cp_violations_after: list[PairViolation] = field(default_factory=list)
    cp_corrected: bool = False
    lut_violations: list[PairViolation] = field(default_factory=list)
    lut_corrected: bool = False
    endpoint_result: tuple[float, float, int, int] = (0.0, 1.0, 0, 255)

    @property
    def is_monotonic(self) -> bool:
        return not self.cp_violations_after and not self.lut_violations


def _check_pairs(points: Sequence[tuple[float, float]]) -> list[PairViolation]:
    """逐一校验相邻控制点是否非递减。"""
    violations: list[PairViolation] = []
    for i in range(1, len(points)):
        x0, y0 = points[i - 1]
        x1, y1 = points[i]
        if y1 < y0 - _EPS:
            violations.append(PairViolation(i, x0, y0, x1, y1))
    return violations


def _isotonic_non_decreasing(values: Sequence[float]) -> list[float]:
    """保序回归（Pool Adjacent Violators Algorithm），返回最近的非递减序列。

    最小化 sum((z_i - v_i)^2)，约束 z_0 <= z_1 <= ...，权重均为 1。
    """
    block_sum: list[float] = []
    block_cnt: list[int] = []
    for v in values:
        block_sum.append(float(v))
        block_cnt.append(1)
        while len(block_sum) >= 2 and block_sum[-1] / block_cnt[-1] < (
            block_sum[-2] / block_cnt[-2]
        ) - _EPS:
            block_sum[-2] += block_sum[-1]
            block_cnt[-2] += block_cnt[-1]
            block_sum.pop()
            block_cnt.pop()
    result: list[float] = []
    for s, n in zip(block_sum, block_cnt):
        result.extend([s / n] * n)
    return result


def _prepare_control_points(
    raw: Sequence[tuple[float, float]], report: CurveBuildReport
) -> list[tuple[float, float]]:
    """排序、合并重复 x、y 夹取、端点强制。"""
    points = [(float(x), float(y)) for x, y in raw]
    for x, _ in points:
        if not (0.0 <= x <= 1.0):
            raise ValueError(f"控制点 x 越界: {x}，必须在 [0, 1]")

    points.sort(key=lambda p: p[0])  # 稳定排序：相同 x 保留输入先后顺序

    merged: list[tuple[float, float]] = []
    for x, y in points:
        if merged and abs(x - merged[-1][0]) <= _EPS:
            report.duplicate_x_merged.append(x)
            merged[-1] = (merged[-1][0], y)  # 同 x 后者覆盖前者
        else:
            merged.append((x, y))

    clamped: list[tuple[float, float]] = []
    for idx, (x, y) in enumerate(merged):
        y2 = min(1.0, max(0.0, y))
        if y2 != y:
            report.y_clamped.append((idx, y, y2))
        clamped.append((x, y2))

    # 端点：存在则强制，缺失则补点
    if abs(clamped[0][0] - 0.0) <= _EPS:
        if abs(clamped[0][1] - 0.0) > _EPS:
            report.endpoint_actions.append(
                f"x=0 控制点 y={clamped[0][1]:.4f}，强制改为 0.0（零点必须映射到零点）"
            )
        clamped[0] = (0.0, 0.0)
    else:
        report.endpoint_actions.append("缺少 x=0 控制点，补点 (0.0, 0.0)")
        clamped.insert(0, (0.0, 0.0))

    if abs(clamped[-1][0] - 1.0) <= _EPS:
        if abs(clamped[-1][1] - 1.0) > _EPS:
            report.endpoint_actions.append(
                f"x=1 控制点 y={clamped[-1][1]:.4f}，强制改为 1.0（满值必须映射到满值）"
            )
        clamped[-1] = (1.0, 1.0)
    else:
        report.endpoint_actions.append("缺少 x=1 控制点，补点 (1.0, 1.0)")
        clamped.append((1.0, 1.0))

    return clamped


def evaluate_curve(points: Sequence[tuple[float, float]], x: float) -> float:
    """在最终控制点曲线上做分段线性求值；x 超出 [0,1] 时按端点值钳制。"""
    xs = [p[0] for p in points]
    if x <= 0.0:
        return points[0][1]
    if x >= 1.0:
        return points[-1][1]
    i = bisect.bisect_right(xs, x) - 1
    if i >= len(points) - 1:
        return points[-1][1]
    x0, y0 = points[i]
    x1, y1 = points[i + 1]
    if x1 == x0:
        return y1
    t = (x - x0) / (x1 - x0)
    return y0 + t * (y1 - y0)


def build_lut(points: Sequence[tuple[float, float]]) -> bytes:
    """由最终控制点构造 256 字节查找表。"""
    table = bytearray(256)
    for k in range(256):
        y = evaluate_curve(points, k / 255.0)
        v = int(min(1.0, max(0.0, y)) * 255.0 + 0.5)  # 传统四舍五入
        table[k] = v
    table[0] = 0
    table[255] = 255
    return bytes(table)


def _check_lut(table: Sequence[int]) -> list[PairViolation]:
    """逐一校验 LUT 255 对相邻项。"""
    violations: list[PairViolation] = []
    for i in range(1, 256):
        if table[i] < table[i - 1]:
            violations.append(
                PairViolation(i, float(i - 1), float(table[i - 1]),
                              float(i), float(table[i]))
            )
    return violations


def _force_lut_monotonic(table: bytes) -> bytes:
    """防御性兜底：累计最大值强制非递减，同时保端点。"""
    out = bytearray(256)
    m = 0
    for i in range(256):
        m = max(m, table[i])
        out[i] = m
    out[0] = 0
    out[255] = 255
    return bytes(out)


class ToneCurve:
    """色调曲线：控制点 -> LUT -> 图像映射。"""

    def __init__(self, control_points: Sequence[tuple[float, float]]):
        self.report = CurveBuildReport(raw_points=list(control_points))
        prepared = _prepare_control_points(control_points, self.report)
        self.report.prepared_points = prepared

        violations = _check_pairs(prepared)
        self.report.cp_violations_before = violations
        if violations:
            self.report.cp_corrected = True
            ys = [y for _, y in prepared]
            fixed = _isotonic_non_decreasing(ys)
            fixed[0] = 0.0
            fixed[-1] = 1.0
            prepared = [(x, fixed[i]) for i, (x, _) in enumerate(prepared)]
        self.report.final_points = prepared
        self.report.cp_violations_after = _check_pairs(prepared)

        table = build_lut(prepared)
        lut_violations = _check_lut(table)
        self.report.lut_violations = lut_violations
        if lut_violations:
            self.report.lut_corrected = True
            table = _force_lut_monotonic(table)
            self.report.lut_violations = _check_lut(table)

        self.lut = table
        self.report.endpoint_result = (
            prepared[0][1], prepared[-1][1], self.lut[0], self.lut[255]
        )

    @property
    def points(self) -> list[tuple[float, float]]:
        return self.report.final_points

    def apply(self, image: bytes | bytearray) -> bytes:
        """对 8 位灰度数据（长度任意的 bytes/bytearray）按 LUT 映射。"""
        if not isinstance(image, (bytes, bytearray)):
            raise TypeError("image 必须是 bytes 或 bytearray")
        return bytes(image).translate(self.lut)

    def evaluate(self, x: float) -> float:
        return evaluate_curve(self.points, x)
