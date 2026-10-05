"""核心 DSP：重采样、WSOLA 变速、变调、复合处理。仅使用标准库。

约定：
  - 采样为 array('f') 浮点序列，量程约 [-1, 1]。
  - tempo: 速度倍数，>1 表示更快（更短），输出时长 = 输入时长 / tempo。
  - pitch: 音高倍数，>1 表示音高升高，输出基频 = 输入基频 * pitch。
"""
import math
from array import array
from operator import mul


def hann_window(n):
    """周期性 Hann 窗（首尾为 0），用于重叠相加。"""
    if n <= 0:
        return []
    if n == 1:
        return [1.0]
    return [0.5 - 0.5 * math.cos(2.0 * math.pi * i / n) for i in range(n)]


def resample(samples, factor):
    """按 factor 重采样：音高 xfactor，时长 /factor。Catmull-Rom 三次插值。"""
    n = len(samples)
    if n == 0:
        return array('f')
    if abs(factor - 1.0) < 1e-12:
        return array('f', samples)
    out_len = max(1, int(round(n / factor)))
    src = samples
    last = n - 1
    out = array('f', [0.0]) * out_len
    for i in range(out_len):
        pos = i * factor
        i1 = int(pos)
        t = pos - i1
        i0 = i1 - 1
        i2 = i1 + 1
        i3 = i1 + 2
        if i0 < 0:
            i0 = 0
        if i1 > last:
            i1 = last
        if i2 > last:
            i2 = last
        if i3 > last:
            i3 = last
        y0 = src[i0]
        y1 = src[i1]
        y2 = src[i2]
        y3 = src[i3]
        out[i] = y1 + 0.5 * t * (y2 - y0 + t * (2.0 * y0 - 5.0 * y1 + 4.0 * y2 - y3
                                                + t * (3.0 * (y1 - y2) + y3 - y0)))
    return out


def _seg_ncc(x, x_off, y, y_off, length, stride=1):
    """两段序列的归一化互相关系数；stride>1 时按步长抽取（粗搜索用）。"""
    if stride == 1:
        xs = x[x_off:x_off + length]
        ys = y[y_off:y_off + length]
    else:
        xs = x[x_off:x_off + length:stride]
        ys = y[y_off:y_off + length:stride]
    ex = sum(map(mul, xs, xs))
    ey = sum(map(mul, ys, ys))
    if ex <= 1e-12 or ey <= 1e-12:
        return -2.0
    return sum(map(mul, xs, ys)) / math.sqrt(ex * ey)


def _wsola_search(xp, y, center, out_pos, search, corr_len):
    """三级搜索：8 倍抽取粗搜 -> 2 倍抽取细化 -> 全精度细化，返回最佳起始位置。"""
    y_energy = sum(map(mul, y[out_pos:out_pos + corr_len], y[out_pos:out_pos + corr_len]))
    if y_energy <= 1e-10:
        return center  # 重叠区是静音，无需对齐，避免 0/0

    # 第一级：8 倍抽取，步长 8
    best = center
    best_v = -2.0
    lo = center - search
    hi = center + search
    d = lo
    while d <= hi:
        v = _seg_ncc(xp, d, y, out_pos, corr_len, stride=8)
        if v > best_v:
            best_v = v
            best = d
        d += 8
    # 第二级：2 倍抽取，步长 2，范围 ±6
    c2 = best
    best_v = -2.0
    for d in range(best - 6, best + 7, 2):
        if d < lo or d > hi:
            continue
        v = _seg_ncc(xp, d, y, out_pos, corr_len, stride=2)
        if v > best_v:
            best_v = v
            c2 = d
    # 第三级：全精度，范围 ±2
    c3 = c2
    best_v = -2.0
    for d in range(c2 - 2, c2 + 3):
        if d < lo or d > hi:
            continue
        v = _seg_ncc(xp, d, y, out_pos, corr_len, stride=1)
        if v > best_v:
            best_v = v
            c3 = d
    return c3


def time_stretch(samples, tempo, frame=2048, search=None):
    """变速不变调（WSOLA）。输出长度 = round(len / tempo)。

    原理：把信号切成重叠的 grain，按合成步长 hs=ha/tempo 重新摆放；
    每个 grain 的截取位置在分析位置附近搜索，使它与已合成输出的重叠区
    波形（相位）对齐，再用 Hann 窗做归一化重叠相加。grain 内部波形不被
    修改，因此基频不变，只有时间轴被重排。
    """
    n = len(samples)
    out_len = int(round(n / tempo))
    if n == 0:
        return array('f')
    if n < frame or abs(tempo - 1.0) < 1e-9:
        out = array('f', samples)
        if len(out) > out_len:
            del out[out_len:]
        elif len(out) < out_len:
            out.extend(array('f', [0.0]) * (out_len - len(out)))
        return out
    if search is None:
        search = frame // 8
    ha = frame // 2
    hs = ha / tempo
    corr_len = frame // 4
    win = hann_window(frame)

    x = samples if isinstance(samples, array) else array('f', samples)
    pad = frame
    xp = array('f', [0.0]) * pad + x + array('f', [0.0]) * pad

    nframes = n // ha + 2
    buf_len = out_len + frame + int(hs) + 2
    y = array('f', [0.0]) * buf_len
    wsum = array('f', [0.0]) * buf_len

    for k in range(nframes):
        ana = k * ha
        if ana >= n + ha:
            break
        out_pos = int(round(k * hs))
        center = pad + ana
        if k == 0:
            best = center
        else:
            best = _wsola_search(xp, y, center, out_pos, search, corr_len)
        seg = xp[best:best + frame]
        cur = y[out_pos:out_pos + frame]
        y[out_pos:out_pos + frame] = array('f',
            [a + b * w for a, b, w in zip(cur, seg, win)])
        ws = wsum[out_pos:out_pos + frame]
        wsum[out_pos:out_pos + frame] = array('f', [a + w for a, w in zip(ws, win)])

    out = array('f', [0.0]) * out_len
    for i in range(out_len):
        w = wsum[i]
        if w > 1e-8:
            out[i] = y[i] / w
    return out


def pitch_shift(samples, pitch, frame=2048):
    """变调不变速。pitch 为频率倍数。输出长度 = len(samples)。

    先重采样（音高 xpitch、时长 /pitch），再用 WSOLA 把时长按相同比例
    拉回去。WSOLA 不改变基频，所以净效果只有音高变化。
    """
    return time_stretch(resample(samples, pitch), tempo=1.0 / pitch, frame=frame)


def process(samples, tempo=1.0, pitch=1.0, frame=2048):
    """变速变调复合处理，两者独立。

    重采样级：音高 xpitch，时长 /pitch。
    WSOLA 级：时长再乘 pitch/tempo，基频不变。
    净效果：时长 /tempo（与 pitch 无关），基频 xpitch（与 tempo 无关）。
    """
    if abs(pitch - 1.0) < 1e-12:
        return time_stretch(samples, tempo, frame=frame)
    stage = resample(samples, pitch)
    return time_stretch(stage, tempo / pitch, frame=frame)
