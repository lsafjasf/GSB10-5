"""seamstitch: 纯标准库图像拼接与重叠融合库（灰度，Python 3）。

核心思想
--------
两张同高灰度图横向排列，B 相对 A 右移 dx 像素，重叠区为 [dx, A.width)。
拼接不沿直线切开，而是在重叠区内用动态规划搜索一条"最小代价接缝"，
接缝左侧取 A、右侧取 B，并在接缝附近做窄带羽化，从而避免直线接缝
穿过主体造成的割裂。

接缝搜索方式（动态规划 / seam carving）
--------------------------------------
对重叠区每一行 y、每个列 x 定义代价：

    cost(x, y) = |A'(x,y) - B(x-dx,y)|            # 补偿后的颜色不一致
               + GRAD_WEIGHT * max(gA(x,y), gB(x-dx,y))   # 内容梯度惩罚

其中 A' 为曝光补偿后的 A，gA/gB 为梯度幅值 |dI/dx| + |dI/dy|。
梯度项让接缝绕开高对比内容（主体边缘），颜色项保证接缝两侧内容一致。

用 DP 自顶向下累积最小代价：

    C(0, x)   = cost(0, x)
    C(y, x)   = cost(y, x) + min(C(y-1, x-1), C(y-1, x), C(y-1, x+1))

最后一行取 argmin 回溯即得每行一个列坐标的路径（8 连通、单调向下）。

曝光补偿
--------
在重叠区估计增益 gain = mean(B) / mean(A)（截断到 [0.5, 2.0]），
将 A 整体乘以 gain 后再搜索接缝与融合。报告中给出补偿前后重叠区
平均绝对亮度差。

小重叠退路
----------
overlap_width <= 0            -> 拒绝拼接（status="rejected"）
0 < overlap_width < MIN_OVERLAP -> 退化为简单线性渐变融合（status="fallback_blend"）
否则                          -> 接缝融合（status="seam"）
"""

import math

GRAD_WEIGHT = 4.0          # 梯度惩罚权重
SUBJECT_GRAD_THRESHOLD = 48  # 梯度超过该值视为"主体/高对比内容"
MIN_OVERLAP = 8            # 小于该重叠宽度走退路
FEATHER = 3                # 接缝两侧羽化半宽


class Image:
    """简单灰度图，pixels[y][x] 为 0..255 的 int。"""

    def __init__(self, width, height, pixels=None):
        self.width = width
        self.height = height
        self.pixels = pixels if pixels is not None else [
            [0] * width for _ in range(height)]

    @classmethod
    def from_rows(cls, rows):
        return cls(len(rows[0]), len(rows), [list(r) for r in rows])

    def copy(self):
        return Image(self.width, self.height, [r[:] for r in self.pixels])


# ---------------- PGM I/O（标准库即可读写，便于自测留存结果） ----------------

def write_pgm(img, path):
    with open(path, "wb") as f:
        f.write(b"P5\n%d %d\n255\n" % (img.width, img.height))
        f.write(bytes(v for row in img.pixels for v in row))


def read_pgm(path):
    with open(path, "rb") as f:
        data = f.read()
    # 解析头部（允许注释行）
    tokens, i = [], 0
    while len(tokens) < 4:
        while i < len(data) and data[i:i+1].isspace():
            i += 1
        if data[i:i+1] == b"#":
            while i < len(data) and data[i:i+1] != b"\n":
                i += 1
            continue
        j = i
        while j < len(data) and not data[j:j+1].isspace():
            j += 1
        tokens.append(data[i:j])
        i = j
    i += 1  # 单个空白分隔符
    assert tokens[0] == b"P5", "仅支持二进制 PGM (P5)"
    w, h = int(tokens[1]), int(tokens[2])
    body = data[i:i + w * h]
    pixels = [list(body[y * w:(y + 1) * w]) for y in range(h)]
    return Image(w, h, pixels)


# ---------------- 基础算子 ----------------

def gradient_magnitude(img):
    """|dI/dx| + |dI/dy|，边界用前向/后向差分。"""
    w, h, p = img.width, img.height, img.pixels
    g = [[0] * w for _ in range(h)]
    for y in range(h):
        row = p[y]
        for x in range(w):
            dx = row[x + 1] - row[x - 1] if 0 < x < w - 1 else (
                row[1] - row[0] if w > 1 else 0)
            if 0 < y < h - 1:
                dy = p[y + 1][x] - p[y - 1][x]
            elif h > 1:
                dy = p[1][0] - p[0][0] if y == 0 else p[h - 1][0] - p[h - 2][0]
            else:
                dy = 0
            g[y][x] = abs(dx) + abs(dy)
    return g


def estimate_gain(a, b, dx):
    """重叠区亮度增益 gain = mean(B)/mean(A)。"""
    ow = a.width - dx
    h = a.height
    sa = sb = 0
    n = ow * h
    for y in range(h):
        ra, rb = a.pixels[y], b.pixels[y]
        for x in range(dx, a.width):
            sa += ra[x]
            sb += rb[x - dx]
    mean_a = sa / n
    mean_b = sb / n
    if mean_a < 1e-6:
        return 1.0
    return min(2.0, max(0.5, mean_b / mean_a))


def apply_gain(img, gain):
    out = img.copy()
    for y in range(out.height):
        row = out.pixels[y]
        for x in range(out.width):
            row[x] = max(0, min(255, int(round(row[x] * gain))))
    return out


def mean_abs_diff(a, b, dx):
    """重叠区平均绝对亮度差（A 与 B 原始值直接比较）。"""
    ow = a.width - dx
    h = a.height
    s = 0
    for y in range(h):
        ra, rb = a.pixels[y], b.pixels[y]
        for x in range(dx, a.width):
            s += abs(ra[x] - rb[x - dx])
    return s / (ow * h)


# ---------------- 接缝搜索 ----------------

def find_seam(a, b, dx, grad_weight=GRAD_WEIGHT):
    """在重叠区搜索最小代价接缝。

    返回 (path, stats)：
      path[y] = 接缝在第 y 行的列坐标（A 的坐标系，dx <= x < a.width）
      stats = {"total_cost", "subject_crossings", "straight_crossings"}
    """
    ow = a.width - dx
    h = a.height
    ga = gradient_magnitude(a)
    gb = gradient_magnitude(b)

    cost = [[0.0] * ow for _ in range(h)]
    subject = [[False] * ow for _ in range(h)]
    for y in range(h):
        ra, rb = a.pixels[y], b.pixels[y]
        gar, gbr = ga[y], gb[y]
        crow, srow = cost[y], subject[y]
        for i in range(ow):
            x = dx + i
            g = gar[x] if gar[x] >= gbr[x - dx] else gbr[x - dx]
            crow[i] = abs(ra[x] - rb[x - dx]) + grad_weight * g
            srow[i] = g > SUBJECT_GRAD_THRESHOLD

    # DP 累积
    acc = [list(r) for r in cost]
    prev = [[0] * ow for _ in range(h)]
    for y in range(1, h):
        arow, prow, pr = acc[y], acc[y - 1], prev[y]
        for i in range(ow):
            best = prow[i]
            bi = i
            if i > 0 and prow[i - 1] < best:
                best = prow[i - 1]
                bi = i - 1
            if i < ow - 1 and prow[i + 1] < best:
                best = prow[i + 1]
                bi = i + 1
            arow[i] += best
            pr[i] = bi

    # 回溯
    last = acc[h - 1]
    i = min(range(ow), key=lambda k: last[k])
    path = [0] * h
    for y in range(h - 1, -1, -1):
        path[y] = dx + i
        i = prev[y][i]

    crossings = sum(1 for y in range(h) if subject[y][path[y] - dx])
    mid = ow // 2
    straight = sum(1 for y in range(h) if subject[y][mid])
    stats = {
        "total_cost": round(min(last), 2),
        "subject_crossings": crossings,
        "straight_crossings": straight,
        "overlap_width": ow,
        "height": h,
    }
    return path, stats


# ---------------- 融合 ----------------

def _composite_base(a, b, dx):
    """画布：宽 dx + b.width，左侧填 A，右侧填 B（重叠区先放 A）。"""
    w = dx + b.width
    h = a.height
    out = Image(w, h)
    for y in range(h):
        row = out.pixels[y]
        row[:a.width] = a.pixels[y]
        row[dx:dx + b.width] = b.pixels[y]
    return out


def blend_seam(a, b, dx, path, feather=FEATHER):
    """沿接缝融合：接缝左取 A，右取 B，接缝附近线性羽化。"""
    out = _composite_base(a, b, dx)
    h = a.height
    for y in range(h):
        s = path[y]
        row = out.pixels[y]
        ra, rb = a.pixels[y], b.pixels[y]
        for x in range(dx, dx + b.width if dx + b.width < out.width else out.width):
            if x >= a.width and x - dx >= b.width:
                continue
            va = ra[x] if x < a.width else None
            vb = rb[x - dx] if x - dx < b.width else None
            if va is None:
                row[x] = vb
            elif vb is None:
                row[x] = va
            else:
                d = x - s
                if d <= -feather:
                    row[x] = va
                elif d >= feather:
                    row[x] = vb
                else:
                    t = (d + feather) / (2.0 * feather)
                    row[x] = int(round(va * (1 - t) + vb * t))
    return out


def blend_linear(a, b, dx):
    """退路：重叠区线性渐变融合。"""
    out = _composite_base(a, b, dx)
    ow = a.width - dx
    for y in range(a.height):
        row = out.pixels[y]
        ra, rb = a.pixels[y], b.pixels[y]
        for i in range(ow):
            t = (i + 1) / (ow + 1)
            row[dx + i] = int(round(ra[dx + i] * (1 - t) + rb[i] * t))
    return out


# ---------------- 主入口 ----------------

def stitch(a, b, dx, min_overlap=MIN_OVERLAP):
    """拼接 A（左）与 B（右，偏移 dx）。返回 (result_or_None, report)。"""
    if a.height != b.height:
        raise ValueError("两图高度必须一致")
    overlap = a.width - dx

    report = {"dx": dx, "overlap_width": overlap,
              "brightness_diff_before": None, "brightness_diff_after": None,
              "gain": None, "seam_path": None, "seam_stats": None}

    if overlap <= 0:
        report["status"] = "rejected"
        report["reason"] = "无重叠区，拒绝拼接"
        return None, report

    # 曝光补偿
    gain = estimate_gain(a, b, dx)
    report["gain"] = round(gain, 4)
    report["brightness_diff_before"] = round(mean_abs_diff(a, b, dx), 3)
    ac = apply_gain(a, gain)
    report["brightness_diff_after"] = round(mean_abs_diff(ac, b, dx), 3)

    if overlap < min_overlap:
        report["status"] = "fallback_blend"
        report["reason"] = ("重叠区宽 %d < 最小要求 %d，"
                            "退化为简单线性融合" % (overlap, min_overlap))
        return blend_linear(ac, b, dx), report

    path, stats = find_seam(ac, b, dx)
    report["status"] = "seam"
    report["seam_path"] = path
    report["seam_stats"] = stats
    return blend_seam(ac, b, dx, path), report
