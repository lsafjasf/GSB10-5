"""denoise.py -- 纯标准库图像去噪库（Python 3，无第三方依赖）。

图像表示：灰度图，扁平 list[int]，行主序，长度 w*h，像素范围 0..255。
提供：
  - PGM (P2/P5) 读写
  - 三种去噪方法：高斯滤波、中值滤波、双边滤波
  - 成对评估指标：PSNR、平坦区残差噪声标准差、边缘梯度保留率、纹理能量保留率
"""
import math


# ---------------- PGM I/O ----------------

def read_pgm(path):
    """读取 PGM (P5 二进制 / P2 文本)，返回 (pixels, w, h)。"""
    with open(path, 'rb') as f:
        data = f.read()
    tokens = []
    i = 0
    while len(tokens) < 4:
        while i < len(data) and data[i:i + 1].isspace():
            i += 1
        if data[i:i + 1] == b'#':
            while i < len(data) and data[i] != 10:
                i += 1
            continue
        j = i
        while j < len(data) and not data[j:j + 1].isspace():
            j += 1
        tokens.append(data[i:j])
        i = j
    magic, w, h, maxval = tokens[0], int(tokens[1]), int(tokens[2]), int(tokens[3])
    i += 1  # maxval 后的单个空白
    if magic == b'P5':
        px = list(data[i:i + w * h])
    elif magic == b'P2':
        px = [int(t) for t in data[i:].split()]
    else:
        raise ValueError('not a PGM file: %r' % magic)
    if maxval != 255:
        px = [round(p * 255 / maxval) for p in px]
    return px, w, h


def write_pgm(path, pixels, w, h):
    """写出二进制 PGM (P5)。"""
    with open(path, 'wb') as f:
        f.write(b'P5\n%d %d\n255\n' % (w, h))
        f.write(bytes(max(0, min(255, int(p))) for p in pixels))


# ---------------- 滤波器 ----------------

def _gaussian_kernel(sigma):
    r = max(1, int(math.ceil(3.0 * sigma)))
    k = [math.exp(-(x * x) / (2.0 * sigma * sigma)) for x in range(-r, r + 1)]
    s = sum(k)
    return [v / s for v in k], r


def gaussian_blur(img, w, h, sigma):
    """高斯滤波（可分离两遍）。适用：高斯型随机噪声；代价：无差别模糊边缘与纹理。"""
    k, r = _gaussian_kernel(sigma)
    tmp = [0.0] * (w * h)
    for y in range(h):
        base = y * w
        for x in range(w):
            acc = 0.0
            for i, kv in enumerate(k):
                xx = x + i - r
                if xx < 0:
                    xx = 0
                elif xx >= w:
                    xx = w - 1
                acc += img[base + xx] * kv
            tmp[base + x] = acc
    out = [0] * (w * h)
    for y in range(h):
        base = y * w
        for x in range(w):
            acc = 0.0
            for i, kv in enumerate(k):
                yy = y + i - r
                if yy < 0:
                    yy = 0
                elif yy >= h:
                    yy = h - 1
                acc += tmp[yy * w + x] * kv
            out[base + x] = int(acc + 0.5)
    return out


def median_filter(img, w, h, size=3):
    """中值滤波。适用：脉冲/椒盐噪声、重噪声下的边缘保持；代价：抹去细纹理、产生斑块感。"""
    r = size // 2
    out = [0] * (w * h)
    for y in range(h):
        y0 = y - r
        if y0 < 0:
            y0 = 0
        y1 = y + r
        if y1 >= h:
            y1 = h - 1
        for x in range(w):
            x0 = x - r
            if x0 < 0:
                x0 = 0
            x1 = x + r
            if x1 >= w:
                x1 = w - 1
            win = [img[yy * w + xx] for yy in range(y0, y1 + 1) for xx in range(x0, x1 + 1)]
            win.sort()
            out[y * w + x] = win[len(win) // 2]
    return out


def bilateral_filter(img, w, h, sigma_s=2.0, sigma_r=30.0, radius=None):
    """双边滤波。空间高斯 × 值域高斯：只平均"亮度相近"的邻居。
    适用：需要保边的场景（边缘两侧亮度差大，被值域核抑制）；代价：最慢，强噪声下值域核失效。"""
    if radius is None:
        radius = max(1, int(math.ceil(2.0 * sigma_s)))
    spatial = []
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            spatial.append(math.exp(-(dx * dx + dy * dy) / (2.0 * sigma_s * sigma_s)))
    lut = [math.exp(-(d * d) / (2.0 * sigma_r * sigma_r)) for d in range(256)]
    out = [0] * (w * h)
    # 内部像素快路径：无需边界钳制，直接用扁平偏移
    taps = []
    ti = 0
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            taps.append((dy * w + dx, spatial[ti]))
            ti += 1
    for y in range(radius, h - radius):
        base = y * w
        for x in range(radius, w - radius):
            idx = base + x
            c = img[idx]
            wsum = 0.0
            acc = 0.0
            for off, sw in taps:
                p = img[idx + off]
                d = p - c
                if d < 0:
                    d = -d
                wt = sw * lut[d]
                acc += p * wt
                wsum += wt
            out[idx] = int(acc / wsum + 0.5)
    # 边界带：钳制坐标
    for y in range(h):
        for x in range(w):
            if radius <= x < w - radius and radius <= y < h - radius:
                continue
            c = img[y * w + x]
            wsum = 0.0
            acc = 0.0
            ti = 0
            for dy in range(-radius, radius + 1):
                yy = y + dy
                if yy < 0:
                    yy = 0
                elif yy >= h:
                    yy = h - 1
                row = yy * w
                for dx in range(-radius, radius + 1):
                    xx = x + dx
                    if xx < 0:
                        xx = 0
                    elif xx >= w:
                        xx = w - 1
                    p = img[row + xx]
                    d = p - c
                    if d < 0:
                        d = -d
                    wt = spatial[ti] * lut[d]
                    acc += p * wt
                    wsum += wt
                    ti += 1
            out[y * w + x] = int(acc / wsum + 0.5)
    return out


# ---------------- 评估指标 ----------------

def mse(a, b):
    return sum((x - y) * (x - y) for x, y in zip(a, b)) / len(a)


def psnr(a, b, peak=255.0):
    """越高越好。噪声下降与细节恢复的综合指标（对干净图）。"""
    m = mse(a, b)
    if m == 0:
        return float('inf')
    return 10.0 * math.log10(peak * peak / m)


def residual_noise_std(img, ref, w, h, rect):
    """平坦区域内 img 相对 ref 的残差标准差 —— 噪声下降指标（越低越好）。"""
    x0, y0, x1, y1 = rect
    diffs = [img[y * w + x] - ref[y * w + x] for y in range(y0, y1) for x in range(x0, x1)]
    n = len(diffs)
    mean = sum(diffs) / n
    return math.sqrt(sum((v - mean) ** 2 for v in diffs) / n)


def mean_abs_gradient(img, w, h, points):
    """指定像素处的平均梯度幅值。"""
    tot = 0.0
    for (x, y) in points:
        gx = img[y * w + min(x + 1, w - 1)] - img[y * w + max(x - 1, 0)]
        gy = img[min(y + 1, h - 1) * w + x] - img[max(y - 1, 0) * w + x]
        tot += 0.5 * math.hypot(gx, gy)
    return tot / len(points)


def edge_retention(out, clean, w, h, points):
    """边缘保留率 = 输出在边缘点的梯度 / 干净图在同点的梯度。1.0 为完美。"""
    return mean_abs_gradient(out, w, h, points) / mean_abs_gradient(clean, w, h, points)


def texture_retention(out, clean, w, h, rect):
    """纹理保留率 = 纹理区水平相邻差分能量比（对干净图归一）。1.0 为完美。"""
    x0, y0, x1, y1 = rect

    def energy(im):
        tot = 0.0
        n = 0
        for y in range(y0, y1):
            base = y * w
            for x in range(x0 + 1, x1 - 1):
                tot += abs(im[base + x + 1] - im[base + x - 1])
                n += 1
        return tot / n

    return energy(out) / energy(clean)
