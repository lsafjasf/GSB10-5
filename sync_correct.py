"""字幕与音频同步校正库（仅标准库）。

模型：audio_time = offset + scale * sub_time
  - 固定偏移：scale == 1，offset != 0（整体早了/晚了固定秒数）
  - 线性漂移：scale != 1（误差随时间线性增大，越往后越偏）
  - 两者叠加：offset != 0 且 scale != 1

判定与拒绝规则（默认值，可通过参数覆盖）：
  - 锚点数 < MIN_ANCHORS(5)           -> 拒绝校正（不做外推）
  - 锚点时间跨度 <= 0                 -> 拒绝校正
  - 漂移总量 |scale-1|*span <= DRIFT_TOLERANCE_S(0.3s) -> 判为固定偏移，
    退化为常数模型（避免用噪声拟合出假漂移）
  - 最大绝对残差 > MAX_RESIDUAL_S(0.5s) -> 报告并拒绝应用（不硬套直线）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import sqrt

MIN_ANCHORS = 5
DRIFT_TOLERANCE_S = 0.3
MAX_RESIDUAL_S = 0.5


@dataclass
class Anchor:
    """一个对齐锚点：字幕时间轴上的时刻对应音频时间轴上的时刻（秒）。"""
    sub_time: float
    audio_time: float


@dataclass
class FitResult:
    ok: bool                 # 是否可应用校正
    kind: str                # 'fixed_offset' | 'linear_drift' | 'rejected'
    offset: float = 0.0      # 常数偏移（秒）
    scale: float = 1.0       # 线性缩放（1 + 漂移率）
    n_anchors: int = 0
    span: float = 0.0
    residuals: list = field(default_factory=list)  # 每个锚点的残差（秒）
    rmse: float = 0.0
    max_abs_residual: float = 0.0
    reason: str = ""

    @property
    def drift_ppm(self) -> float:
        return (self.scale - 1.0) * 1e6

    def correct(self, t: float) -> float:
        return self.offset + self.scale * t


def fit(anchors, min_anchors=MIN_ANCHORS,
        drift_tol=DRIFT_TOLERANCE_S, max_resid=MAX_RESIDUAL_S) -> FitResult:
    """对锚点做最小二乘拟合，判定固定偏移 / 线性漂移，并校验残差。"""
    anchors = sorted(anchors, key=lambda a: a.sub_time)
    n = len(anchors)
    if n < min_anchors:
        return FitResult(ok=False, kind="rejected", n_anchors=n,
                         reason=f"锚点不足：{n} < {min_anchors}，拒绝外推整条时间轴")
    xs = [a.sub_time for a in anchors]
    ys = [a.audio_time for a in anchors]
    span = xs[-1] - xs[0]
    if span <= 0:
        return FitResult(ok=False, kind="rejected", n_anchors=n, span=span,
                         reason="锚点时间跨度为 0，无法拟合")

    # 最小二乘：y = a + b*x
    sx = sum(xs)
    sy = sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sxx - sx * sx
    b = (n * sxy - sx * sy) / denom
    a = (sy - b * sx) / n

    drift_effect = abs(b - 1.0) * span
    if drift_effect <= drift_tol:
        # 漂移不显著 -> 固定偏移，退化为常数模型
        kind = "fixed_offset"
        scale = 1.0
        offset = sum(y - x for x, y in zip(xs, ys)) / n
    else:
        kind = "linear_drift"
        scale = b
        offset = a

    residuals = [y - (offset + scale * x) for x, y in zip(xs, ys)]
    rmse = sqrt(sum(r * r for r in residuals) / n)
    max_abs = max(abs(r) for r in residuals)
    result = FitResult(ok=True, kind=kind, offset=offset, scale=scale,
                       n_anchors=n, span=span, residuals=residuals,
                       rmse=rmse, max_abs_residual=max_abs)
    if max_abs > max_resid:
        result.ok = False
        result.reason = (f"残差过大：max|residual|={max_abs:.3f}s > {max_resid}s，"
                         f"锚点可能含离群值或不是单纯偏移/漂移，拒绝硬套直线")
    return result


def paired_errors(fit_result: FitResult, anchors):
    """校正前后误差成对给出：err = audio_time - corrected(sub_time)。"""
    before, after = [], []
    for a in sorted(anchors, key=lambda x: x.sub_time):
        before.append(a.audio_time - a.sub_time)
        after.append(a.audio_time - fit_result.correct(a.sub_time))
    return before, after


def rmse_of(errors) -> float:
    return sqrt(sum(e * e for e in errors) / len(errors)) if errors else 0.0


# ---------------- SRT 解析 / 生成 / 校正 ----------------

def parse_timestamp(s: str) -> float:
    hh, mm, rest = s.strip().split(":")
    ss, ms = rest.split(",")
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0


def format_timestamp(t: float) -> str:
    if t < 0:
        t = 0.0
    ms = int(round(t * 1000))
    hh, ms = divmod(ms, 3600000)
    mm, ms = divmod(ms, 60000)
    ss, ms = divmod(ms, 1000)
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"


def parse_srt(text: str):
    """返回 [(start, end, payload)]，payload 含序号与文本行。"""
    cues = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        lines = block.strip("\n").split("\n")
        if len(lines) < 2 or "-->" not in lines[1]:
            continue
        t0, t1 = lines[1].split("-->")
        cues.append((parse_timestamp(t0), parse_timestamp(t1),
                     "\n".join(lines[2:])))
    return cues


def correct_cues(cues, fit_result: FitResult):
    return [(fit_result.correct(s), fit_result.correct(e), p)
            for s, e, p in cues]


def dump_srt(cues) -> str:
    out = []
    for i, (s, e, p) in enumerate(cues, 1):
        out.append(f"{i}\n{format_timestamp(s)} --> {format_timestamp(e)}\n{p}\n")
    return "\n".join(out)
