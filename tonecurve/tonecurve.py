"""色调曲线映射库（仅 Python 标准库）。

曲线构造方式
============
输入: 控制点列表 [(x0, y0), (x1, y1), ...]，x/y 为 0..255 的数（允许浮点）。

构造步骤（按顺序执行，每一步的异常都会记录到 BuildReport）:
  1. 归一化: 按 x 升序排序; 相同 x 的 y 取平均合并。
  2. 端点处理: 强制钉住端点 (0,0) 与 (255,255)。
     用户提供的 x=0 / x=255 的 y 值会被覆盖并记录;
     用户未提供端点时自动补 (0,0) / (255,255) 并记录。
     保证零点映射到零点、满值映射到满值。
  3. 越界修正: y 不在 [0,255] 的控制点先记录，再钳制到 [0,255]。
  4. 插值方法: 相邻控制点之间做分段线性插值（piecewise linear），
     铺满 0..255 共 256 项浮点曲线。不引入样条，避免过冲/振荡。
  5. 单调性校验与修正（两级）:
     a. 浮点级: 对归一化后的控制点逐段检查斜率是否非负；
        若存在下降段（非单调），用 PAVA 保序回归（pool adjacent
        violators，等权）把 y 修正为非递减序列，修正量最小（L2 意义）。
     b. 整数级: 浮点曲线四舍五入（half-up）成整数 LUT 后，再做一次
        前缀 cummax 扫描，消除量化引入的微小下降，保证最终 LUT 非递减。
  6. 校验结果: 原始控制点是否单调、是否修正、修正点列表、
     最终 LUT 反转对数（必须为 0）都写入 BuildReport。

映射方式
========
  - apply_lut: 整图经 256 项 LUT 查表（bytes.translate，生产路径）。
  - map_pointwise: 逐点对控制点做二分 + 分段线性插值（独立参考实现，
    用于与 LUT 结果对拍，允许四舍五入造成的 ±1 差异）。
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field

DEPTH = 256
BLACK, WHITE = 0, 255


@dataclass
class BuildReport:
    actions: list = field(default_factory=list)          # 构造过程中的处理记录
    points_in: list = field(default_factory=list)        # 原始控制点
    points_normalized: list = field(default_factory=list)  # 排序/端点/钳制后
    points_corrected: list = field(default_factory=list)   # 单调性修正后
    raw_monotonic: bool = True                           # 原始控制点是否单调不减
    raw_violations: list = field(default_factory=list)     # 原始曲线的下降段
    out_of_range: list = field(default_factory=list)       # 越界控制点
    endpoint_fixes: list = field(default_factory=list)     # 端点覆盖/补齐记录
    monotonicity_fixed: bool = False                     # 是否做了保序修正
    fix_detail: list = field(default_factory=list)         # 被修正的点 (x, 旧y, 新y)
    lut_inversions_before: int = 0                       # 整数化后 cummax 前的反转对数
    lut_inversions_after: int = 0                        # 最终 LUT 反转对数（必须为 0）
    final_monotonic: bool = True                         # 最终 LUT 是否单调不减
    endpoints_ok: bool = True                            # LUT[0]==0 且 LUT[255]==255

    def summary_lines(self):
        lines = []
        lines.append("原始控制点单调性: %s" % ("单调不减" if self.raw_monotonic else "非单调(存在下降段)"))
        for seg in self.raw_violations:
            lines.append("  下降段: (x=%g,y=%g) -> (x=%g,y=%g)" % seg)
        if self.out_of_range:
            lines.append("越界控制点: " + ", ".join("(x=%g,y=%g)->钳制为%g" % t for t in self.out_of_range))
        if self.endpoint_fixes:
            lines.append("端点处理: " + "; ".join(self.endpoint_fixes))
        if self.monotonicity_fixed:
            lines.append("单调性修正(PAVA保序回归): 修正 %d 个点" % len(self.fix_detail))
            for x, old, new in self.fix_detail:
                lines.append("  x=%g: y %g -> %g" % (x, old, new))
        else:
            lines.append("单调性修正: 无需修正")
        if self.lut_inversions_before:
            lines.append("整数量化引入反转 %d 处, 已用 cummax 消除" % self.lut_inversions_before)
        lines.append("最终 LUT 单调性: %s (反转对数=%d)" % ("单调不减" if self.final_monotonic else "非单调!", self.lut_inversions_after))
        lines.append("端点边界: LUT[0]=%d, LUT[255]=%d -> %s" % (
            0, 255, "满足 0->0, 255->255" if self.endpoints_ok else "不满足!"))
        return lines


def _round_half_up(v):
    return int(math.floor(v + 0.5))


def _pava_nondecreasing(ys):
    """等权 PAVA 保序回归，返回非递减序列。修正前后不同的下标也一并返回。"""
    # 每个块: [和, 计数]
    blocks = [[y, 1] for y in ys]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][0] / blocks[i][1] > blocks[i + 1][0] / blocks[i + 1][1]:
            blocks[i][0] += blocks[i + 1][0]
            blocks[i][1] += blocks[i + 1][1]
            del blocks[i + 1]
            if i > 0:
                i -= 1
        else:
            i += 1
    out = []
    for s, c in blocks:
        out.extend([s / c] * c)
    changed = [idx for idx, (a, b) in enumerate(zip(ys, out)) if abs(a - b) > 1e-12]
    return out, changed


def _normalize(points, report):
    pts = [(float(x), float(y)) for x, y in points]
    pts.sort(key=lambda p: p[0])
    # 合并相同 x
    merged = []
    for x, y in pts:
        if merged and merged[-1][0] == x:
            px, py = merged[-1]
            merged[-1] = (px, (py + y) / 2.0)
            report.actions.append("重复控制点 x=%g 的 y 取平均" % x)
        else:
            merged.append((x, y))
    # 端点钉住 (0,0) 与 (255,255)
    if merged and merged[0][0] == BLACK:
        if merged[0][1] != 0.0:
            report.endpoint_fixes.append("覆盖用户端点 (0,%g) -> (0,0)" % merged[0][1])
        merged[0] = (0.0, 0.0)
    else:
        merged.insert(0, (0.0, 0.0))
        report.endpoint_fixes.append("补齐左端点 (0,0)")
    if merged[-1][0] == WHITE:
        if merged[-1][1] != 255.0:
            report.endpoint_fixes.append("覆盖用户端点 (255,%g) -> (255,255)" % merged[-1][1])
        merged[-1] = (255.0, 255.0)
    else:
        merged.append((255.0, 255.0))
        report.endpoint_fixes.append("补齐右端点 (255,255)")
    # 越界钳制
    clamped = []
    for x, y in merged:
        if y < 0.0 or y > 255.0:
            ny = min(255.0, max(0.0, y))
            report.out_of_range.append((x, y, ny))
            y = ny
        clamped.append((x, y))
    return clamped


def build_lut(points):
    """由控制点构造 256 项整数 LUT，返回 (lut, BuildReport)。"""
    report = BuildReport(points_in=list(points))
    pts = _normalize(points, report)
    report.points_normalized = list(pts)

    # 原始（归一化后、修正前）单调性逐段校验
    ys = [y for _, y in pts]
    for i in range(len(pts) - 1):
        if ys[i + 1] < ys[i] - 1e-12:
            report.raw_monotonic = False
            report.raw_violations.append((pts[i][0], ys[i], pts[i + 1][0], ys[i + 1]))
    if not report.raw_monotonic:
        fixed, changed = _pava_nondecreasing(ys)
        report.monotonicity_fixed = True
        report.fix_detail = [(pts[i][0], ys[i], fixed[i]) for i in changed]
        pts = [(x, fixed[i]) for i, (x, _) in enumerate(pts)]
    report.points_corrected = list(pts)

    # 分段线性插值铺满 0..255
    xs = [x for x, _ in pts]
    ys = [y for _, y in pts]
    lutf = []
    seg = 0
    for x in range(DEPTH):
        while seg < len(xs) - 2 and x > xs[seg + 1]:
            seg += 1
        x0, x1 = xs[seg], xs[seg + 1]
        y0, y1 = ys[seg], ys[seg + 1]
        if x1 == x0:
            lutf.append(y1)
        else:
            t = (x - x0) / (x1 - x0)
            lutf.append(y0 + t * (y1 - y0))

    # 整数化 + 整数级单调保证（cummax）
    lut = [_round_half_up(v) for v in lutf]
    inv_before = sum(1 for i in range(DEPTH - 1) if lut[i + 1] < lut[i])
    report.lut_inversions_before = inv_before
    if inv_before:
        for i in range(1, DEPTH):
            if lut[i] < lut[i - 1]:
                lut[i] = lut[i - 1]
    report.lut_inversions_after = sum(1 for i in range(DEPTH - 1) if lut[i + 1] < lut[i])
    report.final_monotonic = report.lut_inversions_after == 0
    report.endpoints_ok = (lut[0] == BLACK and lut[DEPTH - 1] == WHITE)
    return lut, report


def map_pointwise(points, data):
    """独立参考实现：逐像素对控制点二分 + 分段线性插值，返回 bytes。"""
    report = BuildReport()
    pts = _normalize(points, report)
    ys = [y for _, y in pts]
    if any(ys[i + 1] < ys[i] - 1e-12 for i in range(len(pts) - 1)):
        fixed, _ = _pava_nondecreasing(ys)
        pts = [(x, fixed[i]) for i, (x, _) in enumerate(pts)]
    xs = [x for x, _ in pts]
    out = bytearray(len(data))
    for i, v in enumerate(data):
        j = bisect.bisect_right(xs, v) - 1
        j = min(max(j, 0), len(xs) - 2)
        x0, x1 = xs[j], xs[j + 1]
        y0, y1 = pts[j][1], pts[j + 1][1]
        t = 0.0 if x1 == x0 else (v - x0) / (x1 - x0)
        out[i] = _round_half_up(y0 + t * (y1 - y0))
    return bytes(out)


def apply_lut(lut, data):
    """生产路径：整图查表。data 为 bytes/bytearray（灰度或 RGB 展开均可）。"""
    return bytes(data).translate(bytes(lut))
