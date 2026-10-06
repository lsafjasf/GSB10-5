"""selftest.py -- 去噪库自测：成对数据 / 参数对照 / 1 万张耗时与内存。
运行：python3 selftest.py        （完整：质量评估 + 1 万张基准）
      python3 selftest.py quick  （跳过 1 万张基准）
"""
import math
import random
import resource
import sys
import time
import tracemalloc

import denoise as dn

W = H = 128
FLAT_RECT = (8, 8, 40, 40)      # 各测试图里人为保证的平坦区
TEX_RECT = (48, 16, 120, 112)   # 纹理图中的棋盘格区域


def clamp8(v):
    return 0 if v < 0 else 255 if v > 255 else int(v + 0.5)


def add_noise(img, sigma, seed):
    rng = random.Random(seed)
    return [clamp8(p + rng.gauss(0.0, sigma)) for p in img]


# ---------------- 测试图（都带一个 32x32 的平坦块用于测噪声） ----------------

def img_smooth(w, h):
    """缓慢灰度渐变 + 左上平坦块(128) + 右下亮圆盘。轻度/重度噪声用的干净底图。"""
    px = [0] * (w * h)
    for y in range(h):
        for x in range(w):
            px[y * w + x] = clamp8(70 + 110.0 * x / w + 35.0 * y / h)
    for y in range(8, 40):
        for x in range(8, 40):
            px[y * w + x] = 128
    cx, cy, r = 96, 96, 20
    for y in range(h):
        for x in range(w):
            if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                px[y * w + x] = 220
    return px


def img_texture(w, h):
    """2 像素棋盘格细密纹理（值 85/145），左上平坦块。"""
    px = [0] * (w * h)
    for y in range(h):
        for x in range(w):
            px[y * w + x] = 85 if (((x // 2) + (y // 2)) % 2 == 0) else 145
    for y in range(8, 40):
        for x in range(8, 40):
            px[y * w + x] = 110
    return px


def img_edges(w, h):
    """两条 200 级强阶跃边缘：x=64 竖直边、y=96 水平边。"""
    px = [0] * (w * h)
    for y in range(h):
        for x in range(w):
            left = 40 if y < 96 else 240
            px[y * w + x] = left if x < 64 else 240 if y < 96 else 40
    for y in range(8, 40):
        for x in range(8, 40):
            px[y * w + x] = 128
    return px


def edge_points(w, h):
    pts = []
    for y in range(8, h - 8, 4):
        pts.append((w // 2, y))
    for x in range(8, w - 8, 4):
        pts.append((x, 96))
    return pts


def disk_edge_points(cx=96, cy=96, r=20, n=32):
    """smooth 底图中亮圆盘的边界点（真实边缘）。"""
    return [(int(cx + r * math.cos(2 * math.pi * i / n)),
             int(cy + r * math.sin(2 * math.pi * i / n))) for i in range(n)]


# ---------------- 评估 ----------------

def method_set(w, h):
    return [
        ('高斯 sigma=1.2', lambda im: dn.gaussian_blur(im, w, h, 1.2)),
        ('中值 3x3',       lambda im: dn.median_filter(im, w, h, 3)),
        ('双边 s=2 r=35',  lambda im: dn.bilateral_filter(im, w, h, 2.0, 35.0)),
    ]


def evaluate_case(name, clean, noisy, w, h, tex_rect, pts):
    print('=' * 92)
    print('情形: %s' % name)
    print('-' * 92)
    print('%-16s | %-10s | %-16s | %-14s | %-14s' %
          ('方法', 'PSNR(dB)', '平坦区残差噪声std', '边缘保留率', '纹理保留率'))
    print('%-16s | %10.2f | %16.2f | %14s | %14s' %
          ('(未处理)', dn.psnr(noisy, clean),
           dn.residual_noise_std(noisy, clean, w, h, FLAT_RECT), '-', '-'))
    rows = []
    for mname, fn in method_set(w, h):
        out = fn(noisy)
        ps = dn.psnr(out, clean)
        ns = dn.residual_noise_std(out, clean, w, h, FLAT_RECT)
        er = dn.edge_retention(out, clean, w, h, pts) if pts else float('nan')
        tr = dn.texture_retention(out, clean, w, h, tex_rect) if tex_rect else float('nan')
        rows.append((mname, ps, ns, er, tr))
        er_s = '%14.3f' % er if er == er else '%14s' % '-'
        tr_s = '%14.3f' % tr if tr == tr else '%14s' % '-'
        print('%-16s | %10.2f | %16.2f | %s | %s' % (mname, ps, ns, er_s, tr_s))
    print()
    return rows


def edge_cross_section(w, h):
    """强阶跃边 y=48 一行，跨过 x=64 的像素值剖面，看边缘附近实际处理效果。"""
    clean = img_edges(w, h)
    noisy = add_noise(clean, 25, 99)
    outs = [(mname, fn(noisy)) for mname, fn in method_set(w, h)]
    xs = list(range(58, 72))
    y = 48
    print('=' * 92)
    print('强阶跃边缘附近剖面（y=48，边缘位于 x=63|64，噪声 sigma=25）')
    print('-' * 92)
    print('x:          ' + ' '.join('%4d' % x for x in xs))
    print('%-11s ' % '干净' + ' '.join('%4d' % clean[y * w + x] for x in xs))
    print('%-11s ' % '含噪' + ' '.join('%4d' % noisy[y * w + x] for x in xs))
    for mname, out in outs:
        print('%-11s ' % mname + ' '.join('%4d' % out[y * w + x] for x in xs))
    print()


# ---------------- 参数对照 ----------------

def param_sweep(w, h):
    pts = edge_points(w, h)
    edge_clean, edge_noisy = img_edges(w, h), add_noise(img_edges(w, h), 25, 77)
    tex_clean, tex_noisy = img_texture(w, h), add_noise(img_texture(w, h), 20, 88)

    def row(label, fn):
        oe = fn(edge_noisy)
        ot = fn(tex_noisy)
        return ('%-22s %8.2f %8.2f %10.3f %10.3f' % (
            label, dn.psnr(oe, edge_clean), dn.psnr(ot, tex_clean),
            dn.edge_retention(oe, edge_clean, w, h, pts),
            dn.texture_retention(ot, tex_clean, w, h, TEX_RECT)))

    print('=' * 92)
    print('参数对照（强边缘图 sigma=25 噪声 / 细密纹理图 sigma=20 噪声）')
    print('-' * 92)
    print('%-22s %8s %8s %10s %10s' %
          ('参数', 'PSNR边缘', 'PSNR纹理', '边缘保留', '纹理保留'))
    for sig in (0.5, 1.0, 1.5, 2.0, 3.0):
        print(row('高斯 sigma=%.1f' % sig,
                  lambda im, s=sig: dn.gaussian_blur(im, w, h, s)))
    for sz in (3, 5, 7):
        print(row('中值 %dx%d' % (sz, sz),
                  lambda im, s=sz: dn.median_filter(im, w, h, s)))
    for sr in (10, 25, 35, 60, 100):
        print(row('双边 s=2 r=%d' % sr,
                  lambda im, r=sr: dn.bilateral_filter(im, w, h, 2.0, float(r))))
    print()


# ---------------- 1 万张基准 ----------------

def _one_stream(method_fn, n, w, h, seed, trace=False):
    """逐张 生成->处理->丢弃。返回 (总墙钟, 纯处理, Py峰值字节, checksum)。"""
    rng = random.Random(seed)
    if trace:
        tracemalloc.start()
    t_wall = t_proc = 0.0
    checksum = 0
    t0 = time.perf_counter()
    for i in range(n):
        img = [clamp8(120 + rng.gauss(0.0, 25.0)) for _ in range(w * h)]
        tp = time.perf_counter()
        out = method_fn(img)
        t_proc += time.perf_counter() - tp
        checksum = (checksum + out[len(out) // 2]) & 0xffffffff
    t_wall = time.perf_counter() - t0
    peak = 0
    if trace:
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    return t_wall, t_proc, peak, checksum


def benchmark(n=10000, w=64, h=64, mem_sample=200):
    print('=' * 92)
    print('1 万张基准：%d 张 %dx%d 灰度图，逐张生成(sigma=25 高斯噪声)->处理->丢弃' % (n, w, h))
    print('内存峰值：tracemalloc 只在 %d 张小批量上采样，避免全程跟踪拖慢计时；RSS 取进程峰值' % mem_sample)
    print('-' * 92)
    print('%-18s %9s %11s %10s %13s %12s' %
          ('方法(参数)', '总耗时s', '纯处理s', '每张ms', 'Py峰值MB', 'RSS峰值MB'))
    base_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    print('%-18s %9s %11s %10s %13s %12.1f' %
          ('(基线进程RSS)', '-', '-', '-', '-', base_rss))
    cases = [
        ('高斯 s=1.0 (7tap)', lambda im: dn.gaussian_blur(im, w, h, 1.0)),
        ('中值 3x3',          lambda im: dn.median_filter(im, w, h, 3)),
        ('双边 s=1 r=30 5x5', lambda im: dn.bilateral_filter(im, w, h, 1.0, 30.0, radius=2)),
    ]
    for i, (name, fn) in enumerate(cases):
        _, _, peak, _ = _one_stream(fn, mem_sample, w, h, 42 + i, trace=True)
        t_wall, t_proc, _, checksum = _one_stream(fn, n, w, h, 20261006 + i, trace=False)
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
        print('%-18s %9.2f %11.2f %10.3f %13.2f %12.1f  (checksum=%d)' % (
            name, t_wall, t_proc, t_proc * 1000.0 / n,
            peak / 1e6, rss, checksum), flush=True)
    print()


def main():
    w = h = W
    pts = edge_points(w, h)
    dpts = disk_edge_points()
    smooth = img_smooth(w, h)
    edges = img_edges(w, h)
    texture = img_texture(w, h)

    evaluate_case('轻度噪声（渐变+平坦块+圆盘，sigma=10）',
                  smooth, add_noise(smooth, 10, 1), w, h, None, dpts)
    evaluate_case('重度噪声（同底图，sigma=40）',
                  smooth, add_noise(smooth, 40, 2), w, h, None, dpts)
    evaluate_case('细密纹理（2px 棋盘格，sigma=20）',
                  texture, add_noise(texture, 20, 3), w, h, TEX_RECT, pts)
    evaluate_case('明显边缘（200 级阶跃，sigma=25）',
                  edges, add_noise(edges, 25, 4), w, h, None, pts)

    edge_cross_section(w, h)
    param_sweep(w, h)

    if len(sys.argv) > 1 and sys.argv[1] == 'quick':
        print('quick 模式：跳过 1 万张基准。')
    else:
        benchmark()


if __name__ == '__main__':
    main()
