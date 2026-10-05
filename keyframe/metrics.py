"""相邻帧特征度量（纯标准库实现）。

所有度量都在 8bit 灰度帧（bytes，长度 = width*height）上计算：

- 直方图差异 chi2：16 桶亮度直方图的卡方距离（0~2）。
  对镜头切换非常敏感，但对“画面内容不变、只是整体移动”的
  快速运动不敏感 —— 这是区分切换与运动的第一依据。
- 空间变化比例 frac：在固定采样网格上逐点比较，亮度差超过
  PIXEL_T 的采样点占比（0~1）。快速运动时画面内容没变但像素
  位置变了，frac 很高 —— 这是区分切换与运动的第二依据。
- 亮度差 dmean：平均亮度差（0~255）。用于识别淡入淡出
  （直方图形状几乎不变、整体亮度漂移）与闪光灯。
"""

from collections import Counter

BINS = 16          # 直方图桶数（256/16，每桶 16 个灰度级）
PIXEL_T = 16       # 判定“像素发生变化”的亮度差阈值
GRID_STEP = 2      # 空间采样步长：每 2x2 像素取 1 个采样点


def histogram(gray):
    """16 桶亮度直方图（返回计数列表，未归一化）。"""
    counts = [0] * BINS
    for value, cnt in Counter(gray).items():
        counts[value >> 4] += cnt
    return counts


def chi2(hist_a, hist_b):
    """两直方图的卡方距离，范围 [0, 2]。0 表示完全一致。"""
    total = 0.0
    for a, b in zip(hist_a, hist_b):
        s = a + b
        if s:
            d = a - b
            total += d * d / s
    n = sum(hist_a)
    return total / n if n else 0.0


def spatial_frac(gray_a, gray_b, width, height, step=GRID_STEP, pixel_t=PIXEL_T):
    """采样网格上“发生变化”的像素占比，范围 [0, 1]。"""
    changed = 0
    total = 0
    for y in range(0, height, step):
        base = y * width
        for x in range(0, width, step):
            i = base + x
            d = gray_a[i] - gray_b[i]
            if d < 0:
                d = -d
            if d > pixel_t:
                changed += 1
            total += 1
    return changed / total if total else 0.0


def mean_luma(gray):
    return sum(gray) / len(gray)


class FrameFeatures:
    """单帧的缓存特征：直方图 + 平均亮度。"""

    __slots__ = ("hist", "mean")

    def __init__(self, gray):
        self.hist = histogram(gray)
        self.mean = mean_luma(gray)


def pair_metrics(fa, fb, gray_a, gray_b, width, height):
    """相邻帧 a->b 的三个度量：(chi2, frac, dmean)。"""
    return (
        chi2(fa.hist, fb.hist),
        spatial_frac(gray_a, gray_b, width, height),
        fb.mean - fa.mean,
    )
