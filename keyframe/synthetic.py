"""合成视频生成器（纯标准库）：用于自测与基准测试。

真实视频解码不在标准库能力范围内，因此用可控的合成场景验证
检测逻辑：每个场景的类型与边界帧号都是精确已知的（人工标注
等价物）。同时提供 RawGrayReader 支持外部解码器（如 ffmpeg）
输出的原始灰度流，处理真实视频。
"""

import math
import random

WIDTH, HEIGHT = 48, 27
FPS = 25


# ---- 基础画面 ------------------------------------------------------
def _scene_a(w, h):
    """竖条纹场景（高频纹理）。"""
    buf = bytearray(w * h)
    for y in range(h):
        for x in range(w):
            buf[y * w + x] = 40 + ((x // 3) % 2) * 120
    return bytes(buf)


def _scene_b(w, h):
    """对角渐变场景（低频平滑）。"""
    buf = bytearray(w * h)
    for y in range(h):
        for x in range(w):
            buf[y * w + x] = int(30 + 190 * (x + y) / (w + h - 2))
    return bytes(buf)


def _noise(gray, rng, amp=2):
    """加轻微传感器噪声，避免度量恒为 0 的不真实情形。"""
    buf = bytearray(gray)
    for i in range(len(buf)):
        v = buf[i] + rng.randint(-amp, amp)
        buf[i] = 0 if v < 0 else (255 if v > 255 else v)
    return bytes(buf)


def _translate(gray, w, h, dx, dy=0):
    """循环平移画面（模拟快速摇镜：内容不变、位置移动）。

    循环移位保证亮度直方图不变，这样真实摇镜的 chi2 恒接近 0，
    而空间变化比例 frac 很高 —— 与硬切换形成干净对照。
    """
    dx %= w
    dy %= h
    buf = bytearray(w * h)
    for y in range(h):
        sy = (y - dy) % h
        row_out = y * w
        row_in = sy * w
        for x in range(w):
            buf[row_out + x] = gray[row_in + (x - dx) % w]
    return bytes(buf)


def _blend(a, b, t):
    """A、B 两帧按 t 线性混合（溶解转场模型）。"""
    it = 1.0 - t
    return bytes(int(av * it + bv * t) for av, bv in zip(a, b))


def _level(gray, k):
    """整体亮度缩放（淡入淡出模型）。"""
    return bytes(min(255, max(0, int(v * k))) for v in gray)


# ---- 场景片段 ------------------------------------------------------
def seg_static(base, n, rng):
    return [_noise(base, rng) for _ in range(n)]


def seg_pan(base, n, w, h, rng, vx=4, vy=0):
    """快速平移运动：内容不变，位置每帧移动 vx 像素。"""
    frames = []
    for k in range(n):
        frames.append(_noise(_translate(base, w, h, vx * k, vy * k), rng))
    return frames


def seg_dissolve(a, b, n, rng):
    """溶解转场：A 线性混合到 B。"""
    frames = []
    for k in range(n):
        t = (k + 1) / (n + 1)
        frames.append(_noise(_blend(a, b, t), rng))
    return frames


def seg_fade_out(base, n, rng):
    return [_noise(_level(base, 1.0 - (k + 1) / (n + 1)), rng)
            for k in range(n)]


def seg_fade_in(base, n, rng):
    return [_noise(_level(base, (k + 1) / (n + 1)), rng)
            for k in range(n)]


def seg_flash(base, rng):
    """单帧闪光灯：整帧瞬时变亮。"""
    return [_level(base, 2.2)]


# ---- 自测片段（含人工标注）-----------------------------------------
def build_test_clip():
    """构造覆盖全部情形的测试片段。

    返回 (frames, ground_truth)。ground_truth:
      cuts:     [帧号...]          硬切换边界（新镜头第一帧）
      graduals: [(start, end)...]  渐变转场区间（含首不含尾）
      motion:   [(start, end)...]  快速运动区间（不应产关键帧）
      statics:  [(start, end)...]  静态区间（不应产关键帧）
      flashes:  [帧号...]          闪光灯帧（不应产关键帧）

    编排（运动段放在同一镜头内部，保证运动前后不是真切换）：
      静态A -> 快速摇镜A -> 静态A |切| 静态B + 闪光灯
      |溶解| 静态A |淡出| 黑场 |淡入| 长静态B |切| 静态A
    """
    rng = random.Random(42)
    w, h = WIDTH, HEIGHT
    a, b = _scene_a(w, h), _scene_b(w, h)
    frames = []
    gt = {"cuts": [], "graduals": [], "motion": [],
          "statics": [], "flashes": []}

    def add(seq):
        start = len(frames)
        frames.extend(seq)
        return start, len(frames)

    s, e = add(seg_static(a, 100, rng)); gt["statics"].append((s, e))
    s, e = add(seg_pan(a, 40, w, h, rng)); gt["motion"].append((s, e))
    s, e = add(seg_static(a, 60, rng)); gt["statics"].append((s, e))
    s, e = add(seg_static(b, 60, rng)); gt["cuts"].append(s)
    gt["statics"].append((s, e))
    s, e = add(seg_flash(b, rng)); gt["flashes"].append(s)
    s, e = add(seg_static(b, 40, rng)); gt["statics"].append((s, e))
    s, e = add(seg_dissolve(b, a, 30, rng)); gt["graduals"].append((s, e))
    s, e = add(seg_static(a, 60, rng)); gt["statics"].append((s, e))
    s, e = add(seg_fade_out(a, 20, rng)); gt["graduals"].append((s, e))
    s, e = add(seg_static(bytes(w * h), 20, rng)); gt["statics"].append((s, e))
    s, e = add(seg_fade_in(b, 20, rng)); gt["graduals"].append((s, e))
    s, e = add(seg_static(b, 400, rng)); gt["statics"].append((s, e))
    s, e = add(seg_static(a, 100, rng)); gt["cuts"].append(s)
    gt["statics"].append((s, e))

    return frames, gt


# ---- 1 小时基准 ----------------------------------------------------
def benchmark_schedule(fps=FPS, seconds=3600):
    """1 小时节目编排（场景连续、转场类型显式标注）。

    段类型：staticA/staticB 静态镜头，panA 快速摇镜（场景A），
    dissolveBA 溶解 B->A，fadeoutA/black/fadeinB 淡出淡入。
    相邻段场景不同即为硬切换（本编排中出现在 staticB->staticA 等
    直接拼接处）。返回 [(kind, n_frames)...]。
    """
    block = [
        ("staticA", 30 * fps),
        ("panA", 5 * fps),          # 快速摇镜，不切镜头
        ("staticA", 25 * fps),
        ("staticB", 30 * fps),      # <- 硬切换 A->B
        ("dissolveBA", 30),         # 溶解 B->A
        ("staticA", 30 * fps),
        ("fadeoutA", 25),           # 淡出到黑
        ("black", 2 * fps),
        ("fadeinB", 25),            # 淡入到 B
        ("staticB", 60 * fps),      # 长静态镜头
        ("staticA", 20 * fps),      # <- 硬切换 B->A
    ]
    plan = []
    remaining = fps * seconds
    while remaining > 0:
        for kind, n in block:
            if remaining <= 0:
                break
            take = min(n, remaining)
            plan.append((kind, take))
            remaining -= take
    return plan


# ---- 真实视频接入（可选）-------------------------------------------
class RawGrayReader:
    """读取外部解码器输出的原始 8bit 灰度流（每帧 width*height 字节）。

    例：ffmpeg -i in.mp4 -vf scale=48:27 -pix_fmt gray -f rawvideo - \
         | python3 run_real.py --width 48 --height 27
    """

    def __init__(self, fileobj, width, height):
        self.fileobj = fileobj
        self.size = width * height

    def __iter__(self):
        while True:
            buf = self.fileobj.read(self.size)
            if len(buf) < self.size:
                return
            yield buf
