"""裁剪框求解：目标比例适配、贴边保护、居中/拒绝退路。

FitPolicy:
- "contain":  裁剪框必须完整包住主体（主体过宽/过高时自动扩大或退化为全图）
- "center":   以主体质心为中心，允许裁掉主体边缘（用于比例极端且主体超长）
- "reject":   主体无法被目标比例框完整包含时拒绝裁剪（返回 None）
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class FitPolicy:
    mode: str = "contain"       # contain | center | reject
    margin: float = 0.12        # 主体四周留白（相对主体尺寸）
    ratio_tolerance: float = 0.01  # 比例误差容忍（|w/h - target| / target）


@dataclass
class CropResult:
    box: tuple          # (x0, y0, x1, y1)，None 表示拒绝
    policy: str
    reason: str
    subject_covered: bool
    oob_ok: bool
    ratio_error: float


def _fit_box(img_w, img_h, target_ratio, tolerance):
    """求图像范围内满足目标比例的最大裁剪框（w,h 整数，<= 原图）。"""
    # 以高为基准：w = h * ratio
    h = img_h
    w = int(round(h * target_ratio))
    if w > img_w:
        w = img_w
        h = int(round(w / target_ratio))
    # 校验比例误差，必要时 ±1 微调
    def err(ww, hh):
        return abs(ww / hh - target_ratio) / target_ratio
    if err(w, h) > tolerance:
        best = (w, h)
        for dw in (-1, 0, 1):
            for dh in (-1, 0, 1):
                ww, hh = w + dw, h + dh
                if 1 <= ww <= img_w and 1 <= hh <= img_h and err(ww, hh) < err(*best):
                    best = (ww, hh)
        w, h = best
    return w, h


def _clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def crop(img_w, img_h, target_ratio, subject_bbox=None,
         subject_centroid=None, policy=None):
    """计算裁剪框。subject_bbox=(x0,y0,x1,y1) 或 None（无主体）。"""
    pol = policy or FitPolicy()
    box_w, box_h = _fit_box(img_w, img_h, target_ratio, pol.ratio_tolerance)

    if subject_bbox is None:
        # 退路：无主体 → 居中裁剪
        x0 = (img_w - box_w) // 2
        y0 = (img_h - box_h) // 2
        return CropResult((x0, y0, x0 + box_w, y0 + box_h),
                          "center_fallback", "no_subject_center_crop",
                          True, True, _ratio_err(box_w, box_h, target_ratio))

    sx0, sy0, sx1, sy1 = subject_bbox
    sw, sh = sx1 - sx0, sy1 - sy0
    if subject_centroid is None:
        subject_centroid = ((sx0 + sx1) // 2, (sy0 + sy1) // 2)
    cx, cy = subject_centroid

    need_w = int(round(sw * (1 + 2 * pol.margin)))
    need_h = int(round(sh * (1 + 2 * pol.margin)))
    max_box_w, max_box_h = box_w, box_h

    # reject 策略：主体加留白无法放进最大框就拒绝
    if pol.mode == "reject" and (need_w > max_box_w or need_h > max_box_h):
        return CropResult(None, "reject",
                          "subject_exceeds_max_box", False, True,
                          _ratio_err(max_box_w, max_box_h, target_ratio))

    if pol.mode == "contain":
        # 扩大裁剪框以包住主体（保持比例）
        if need_w > box_w:
            box_w = min(img_w, need_w)
            box_h = int(round(box_w / target_ratio))
            if box_h > img_h:
                box_h = img_h
                box_w = min(img_w, int(round(box_h * target_ratio)))
        if need_h > box_h:
            box_h = min(img_h, need_h)
            box_w = int(round(box_h * target_ratio))
            if box_w > img_w:
                box_w = img_w
                box_h = min(img_h, int(round(box_w / target_ratio)))
        # 若主体仍超出框（比例极端），退化为最大框并标记未完全覆盖
        if need_w > box_w or need_h > box_h:
            box_w, box_h = max_box_w, max_box_h
            x0 = _clamp(cx - box_w // 2, 0, img_w - box_w)
            y0 = _clamp(cy - box_h // 2, 0, img_h - box_h)
            return CropResult((x0, y0, x0 + box_w, y0 + box_h),
                              "contain_partial",
                              "subject_larger_than_max_box",
                              False, True,
                              _ratio_err(box_w, box_h, target_ratio))

    # 以主体质心为中心放置，再夹紧到图像范围（贴边保护的核心：
    # 夹紧会把框推向图像内，主体贴边时框贴着图像边，主体不会被裁掉）
    x0 = _clamp(cx - box_w // 2, 0, img_w - box_w)
    y0 = _clamp(cy - box_h // 2, 0, img_h - box_h)

    # 校验主体是否被完整覆盖
    covered = (x0 <= sx0 and y0 <= sy0
               and x0 + box_w >= sx1 and y0 + box_h >= sy1)
    if not covered and pol.mode == "contain":
        # 微调：向主体方向平移
        if x0 > sx0: x0 = sx0
        if y0 > sy0: y0 = sy0
        if x0 + box_w < sx1: x0 = sx1 - box_w
        if y0 + box_h < sy1: y0 = sy1 - box_h
        x0 = _clamp(x0, 0, img_w - box_w)
        y0 = _clamp(y0, 0, img_h - box_h)
        covered = (x0 <= sx0 and y0 <= sy0
                   and x0 + box_w >= sx1 and y0 + box_h >= sy1)

    return CropResult((x0, y0, x0 + box_w, y0 + box_h),
                      pol.mode, "ok", covered, True,
                      _ratio_err(box_w, box_h, target_ratio))


def _ratio_err(w, h, target):
    return abs(w / h - target) / target


def validate_box(box, img_w, img_h, target_ratio, tolerance=0.01):
    """越界与比例校验，返回校验数据字典。"""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    checks = {
        "box": [x0, y0, x1, y1],
        "width": w,
        "height": h,
        "x0_in_range": 0 <= x0 < img_w,
        "y0_in_range": 0 <= y0 < img_h,
        "x1_in_range": 0 < x1 <= img_w,
        "y1_in_range": 0 < y1 <= img_h,
        "positive_size": w > 0 and h > 0,
        "ratio_target": target_ratio,
        "ratio_actual": w / h,
        "ratio_error": _ratio_err(w, h, target_ratio),
        "ratio_ok": _ratio_err(w, h, target_ratio) <= tolerance,
    }
    checks["oob_ok"] = (checks["x0_in_range"] and checks["y0_in_range"]
                        and checks["x1_in_range"] and checks["y1_in_range"]
                        and checks["positive_size"])
    checks["all_ok"] = checks["oob_ok"] and checks["ratio_ok"]
    return checks
