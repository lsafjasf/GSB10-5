"""字幕与音频同步校正库（纯标准库）。

模型
----
锚点 (t_sub, t_audio) 表示：字幕上标记在 t_sub 时刻的事件，在音频中实际
发生在 t_audio 时刻。假设两者满足线性关系：

    t_audio ~= a * t_sub + b

- a == 1, b != 0 : 固定偏移（整体早了/晚了 b 秒）
- a != 1, b == 0 : 线性漂移（越往后偏得越多，帧率/采样率不匹配的典型表现）
- a != 1, b != 0 : 两者叠加

校正即把每条字幕时间 t 替换为 a*t + b，使其对齐音频时间轴。

判定依据（见 classify()）
------------------------
用最小二乘拟合 (a, b)，并计算参数标准误 SE(a)、SE(b)：
- 漂移显著：|a - 1| > DRIFT_SLOPE_TOL 且 |a - 1| > SIGMA_FACTOR * SE(a)
- 偏移显著：|b| > OFFSET_TOL 且 |b| > SIGMA_FACTOR * OFFSET_NOISE_FLOOR
  其中 OFFSET_NOISE_FLOOR 取 SE(b) 与锚点残差 RMS 的较大者，
  避免把锚点测量噪声当成固定偏移。

安全约束
--------
- 锚点数 < MIN_ANCHORS： refuses to fit（少量点外推整条时间轴风险太大）。
- 拟合后残差 RMS > MAX_RMS_RESIDUAL 或最大绝对残差 > MAX_ABS_RESIDUAL：
  判定为"线性模型不成立"（例如中途跳变、锚点标注错误），报告残差并拒绝
  校正，绝不硬套一条直线。
- 拟合前做一轮 MAD 粗差剔除（默认 3.5 倍修正 MAD），被剔除的锚点会
  在报告中列出，不计入最终拟合。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

# ---- 默认阈值（均可通过 SyncConfig 覆盖） ----
MIN_ANCHORS = 6            # 最少可用锚点数，少于此数拒绝校正
DRIFT_SLOPE_TOL = 2e-4     # |a-1| 超过此值才认为存在漂移（= 每小时 0.72s）
OFFSET_TOL = 0.05          # |b| 超过此值（秒）才认为存在固定偏移
SIGMA_FACTOR = 3.0         # 显著性检验的 sigma 倍数
MAX_RMS_RESIDUAL = 0.30    # 残差 RMS 上限（秒），超过则拒绝校正
MAX_ABS_RESIDUAL = 1.00    # 单点最大绝对残差上限（秒）
MAD_OUTLIER_FACTOR = 3.5   # 粗差剔除阈值（修正 MAD 的倍数）


@dataclass
class SyncConfig:
    min_anchors: int = MIN_ANCHORS
    drift_slope_tol: float = DRIFT_SLOPE_TOL
    offset_tol: float = OFFSET_TOL
    sigma_factor: float = SIGMA_FACTOR
    max_rms_residual: float = MAX_RMS_RESIDUAL
    max_abs_residual: float = MAX_ABS_RESIDUAL
    mad_outlier_factor: float = MAD_OUTLIER_FACTOR


@dataclass
class Anchor:
    t_sub: float    # 字幕时间轴上的时刻（秒）
    t_audio: float  # 音频时间轴上对应事件的时刻（秒）


@dataclass
class FitReport:
    """拟合与判定的完整报告。"""
    accepted: bool                     # 是否允许应用校正
    kind: str                          # in_sync / fixed_offset / linear_drift / offset_and_drift / rejected_*
    reason: str                        # 人类可读的判定/拒绝原因
    slope: float = 1.0                 # a
    intercept: float = 0.0             # b
    slope_stderr: float = 0.0
    intercept_stderr: float = 0.0
    n_used: int = 0
    n_outliers: int = 0
    outlier_indices: List[int] = field(default_factory=list)
    residuals: List[float] = field(default_factory=list)   # 每个在用锚点的残差 t_audio - (a*t_sub+b)
    rms_residual: float = 0.0
    max_abs_residual: float = 0.0

    @property
    def drift_seconds_per_hour(self) -> float:
        return (self.slope - 1.0) * 3600.0


def _least_squares(xs: Sequence[float], ys: Sequence[float]) -> Tuple[float, float, float, float, List[float]]:
    """返回 (slope, intercept, slope_stderr, intercept_stderr, residuals)。"""
    n = len(xs)
    sx = sum(xs)
    sy = sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sxx - sx * sx
    if denom <= 0.0:
        raise ValueError("锚点时刻不能全部相同，无法拟合斜率")
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    residuals = [y - (slope * x + intercept) for x, y in zip(xs, ys)]
    if n > 2:
        s2 = sum(r * r for r in residuals) / (n - 2)
    else:
        s2 = 0.0
    sxx_dev = sxx - sx * sx / n
    slope_se = math.sqrt(s2 / sxx_dev) if sxx_dev > 0 else 0.0
    intercept_se = math.sqrt(s2 * sxx / (n * sxx_dev)) if sxx_dev > 0 else 0.0
    return slope, intercept, slope_se, intercept_se, residuals


def _mad_outlier_mask(residuals: Sequence[float], factor: float) -> List[bool]:
    """基于修正 MAD 的粗差掩码，True 表示粗差。"""
    n = len(residuals)
    if n < 4:
        return [False] * n
    srt = sorted(residuals)
    med = srt[n // 2] if n % 2 else 0.5 * (srt[n // 2 - 1] + srt[n // 2])
    dev = sorted(abs(r - med) for r in residuals)
    mad = dev[n // 2] if n % 2 else 0.5 * (dev[n // 2 - 1] + dev[n // 2])
    sigma = 1.4826 * mad
    if sigma <= 1e-12:
        return [False] * n
    return [abs(r - med) > factor * sigma for r in residuals]


def fit_and_classify(anchors: Sequence[Anchor], config: Optional[SyncConfig] = None) -> FitReport:
    """拟合锚点并判定偏移类型；不合格时返回 accepted=False 及原因。"""
    cfg = config or SyncConfig()
    n = len(anchors)
    if n < cfg.min_anchors:
        return FitReport(
            accepted=False,
            kind="rejected_too_few_anchors",
            reason=(f"可用锚点 {n} 个，少于下限 {cfg.min_anchors} 个；"
                    f"拒绝用少量点外推整条时间轴"),
            n_used=0,
        )

    xs = [a.t_sub for a in anchors]
    ys = [a.t_audio for a in anchors]

    # 第一轮拟合 + MAD 粗差剔除
    slope, intercept, _, _, residuals = _least_squares(xs, ys)
    mask = _mad_outlier_mask(residuals, cfg.mad_outlier_factor)
    outlier_indices = [i for i, m in enumerate(mask) if m]
    keep = [i for i, m in enumerate(mask) if not m]
    if len(keep) < cfg.min_anchors:
        return FitReport(
            accepted=False,
            kind="rejected_too_few_anchors",
            reason=(f"剔除 {len(outlier_indices)} 个粗差后仅剩 {len(keep)} 个锚点，"
                    f"少于下限 {cfg.min_anchors} 个"),
            n_used=len(keep),
            n_outliers=len(outlier_indices),
            outlier_indices=outlier_indices,
        )

    kx = [xs[i] for i in keep]
    ky = [ys[i] for i in keep]
    slope, intercept, slope_se, intercept_se, residuals = _least_squares(kx, ky)

    rms = math.sqrt(sum(r * r for r in residuals) / len(residuals))
    max_abs = max(abs(r) for r in residuals)

    # 残差过大：线性模型不成立，报告并拒绝
    if rms > cfg.max_rms_residual or max_abs > cfg.max_abs_residual:
        return FitReport(
            accepted=False,
            kind="rejected_poor_fit",
            reason=(f"线性拟合残差过大：RMS={rms:.4f}s（上限 {cfg.max_rms_residual}s），"
                    f"最大绝对残差={max_abs:.4f}s（上限 {cfg.max_abs_residual}s）。"
                    f"误差不是单纯的固定偏移/线性漂移（可能存在中途跳变或锚点错误），"
                    f"拒绝硬套直线"),
            slope=slope, intercept=intercept,
            slope_stderr=slope_se, intercept_stderr=intercept_se,
            n_used=len(keep), n_outliers=len(outlier_indices),
            outlier_indices=outlier_indices,
            residuals=residuals, rms_residual=rms, max_abs_residual=max_abs,
        )

    # 判定：固定偏移 vs 线性漂移
    drift_dev = abs(slope - 1.0)
    has_drift = (drift_dev > cfg.drift_slope_tol
                 and drift_dev > cfg.sigma_factor * slope_se)
    offset_noise_floor = max(intercept_se, rms)
    has_offset = (abs(intercept) > cfg.offset_tol
                  and abs(intercept) > cfg.sigma_factor * offset_noise_floor)

    if has_drift and has_offset:
        kind = "offset_and_drift"
        desc = (f"固定偏移 {intercept:+.3f}s + 线性漂移 "
                f"{(slope - 1) * 3600:+.3f}s/小时（斜率 {slope:.6f}）")
    elif has_drift:
        kind = "linear_drift"
        desc = (f"线性漂移 {(slope - 1) * 3600:+.3f}s/小时（斜率 {slope:.6f}），"
                f"越往后偏得越多")
    elif has_offset:
        kind = "fixed_offset"
        desc = f"固定偏移 {intercept:+.3f}s（整体{'晚' if intercept > 0 else '早'}了）"
        # 纯固定偏移时把斜率锁为 1，避免拟合噪声引入虚假漂移
        intercept = sum(y - x for x, y in zip(kx, ky)) / len(kx)
        slope = 1.0
        residuals = [y - (x + intercept) for x, y in zip(kx, ky)]
        rms = math.sqrt(sum(r * r for r in residuals) / len(residuals))
        max_abs = max(abs(r) for r in residuals)
    else:
        kind = "in_sync"
        desc = (f"偏移 {intercept:+.3f}s、漂移 {(slope - 1) * 3600:+.3f}s/小时 "
                f"均在容差内，无需校正")
        slope, intercept = 1.0, 0.0

    return FitReport(
        accepted=True,
        kind=kind,
        reason=desc,
        slope=slope, intercept=intercept,
        slope_stderr=slope_se, intercept_stderr=intercept_se,
        n_used=len(keep), n_outliers=len(outlier_indices),
        outlier_indices=outlier_indices,
        residuals=residuals, rms_residual=rms, max_abs_residual=max_abs,
    )


def correct_time(t: float, report: FitReport) -> float:
    """把字幕时间轴上的时刻 t 映射到音频时间轴。"""
    return report.slope * t + report.intercept


def correct_cues(cues: Sequence[Tuple[float, float]], report: FitReport) -> List[Tuple[float, float]]:
    """批量校正 (start, end) 字幕时间段。未通过检验的报告拒绝执行。"""
    if not report.accepted:
        raise ValueError(f"拟合未通过检验（{report.kind}），禁止应用校正：{report.reason}")
    a, b = report.slope, report.intercept
    return [(a * s + b, a * e + b) for s, e in cues]


def paired_errors(anchors: Sequence[Anchor], report: Optional[FitReport] = None
                  ) -> List[Tuple[float, float, float]]:
    """每个锚点的 (t_sub, 校正前误差, 校正后误差)。

    校正前误差 = t_sub - t_audio（字幕时间相对音频真实时间的偏差）。
    校正后误差 = a*t_sub + b - t_audio；report 为 None 时等于校正前误差。
    """
    rows = []
    for a in anchors:
        before = a.t_sub - a.t_audio
        after = (report.slope * a.t_sub + report.intercept - a.t_audio) if report else before
        rows.append((a.t_sub, before, after))
    return rows


def rms(values: Sequence[float]) -> float:
    return math.sqrt(sum(v * v for v in values) / len(values)) if values else 0.0


# ---------------- SRT 读写 ----------------

def parse_srt_time(text: str) -> float:
    """'HH:MM:SS,mmm' -> 秒。"""
    hms, ms = text.strip().replace(".", ",").split(",")
    h, m, s = (int(p) for p in hms.split(":"))
    return h * 3600 + m * 60 + s + int(ms) / 1000.0


def format_srt_time(t: float) -> str:
    if t < 0:
        t = 0.0
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def correct_srt(srt_text: str, report: FitReport) -> str:
    """校正整段 SRT 文本中的时间戳。"""
    if not report.accepted:
        raise ValueError(f"拟合未通过检验（{report.kind}），禁止应用校正")
    out_lines = []
    for line in srt_text.splitlines():
        if "-->" in line:
            left, right = line.split("-->")
            start = parse_srt_time(left)
            end = parse_srt_time(right)
            out_lines.append(f"{format_srt_time(correct_time(start, report))} --> "
                             f"{format_srt_time(correct_time(end, report))}")
        else:
            out_lines.append(line)
    return "\n".join(out_lines) + "\n"
