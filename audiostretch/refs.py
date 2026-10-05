"""参考实现（朴素但原理直白，用于对拍）。"""
from array import array

from .core import hann_window


def ref_resample_linear(samples, factor):
    """线性插值重采样：音高 xfactor、时长 /factor（重采样的“朴素正确”基准）。"""
    n = len(samples)
    out_len = int(round(n / factor))
    out = array('f', [0.0]) * out_len
    for i in range(out_len):
        pos = i * factor
        i1 = int(pos)
        t = pos - i1
        y1 = samples[i1]
        y2 = samples[i1 + 1] if i1 + 1 < n else y1
        out[i] = y1 + (y2 - y1) * t
    return out


def ref_ola_stretch(samples, tempo, frame=2048):
    """无相位搜索的朴素固定步长 OLA 变速（只做时长对拍与基频对拍）。"""
    n = len(samples)
    out_len = int(round(n / tempo))
    if n < frame:
        out = array('f', samples)
        del out[out_len:]
        return out
    ha = frame // 2
    hs = ha / tempo
    win = hann_window(frame)
    nframes = n // ha + 2
    y = array('f', [0.0]) * (out_len + frame + int(hs) + 2)
    wsum = array('f', [0.0]) * len(y)
    for k in range(nframes):
        if k * ha >= n + ha:
            break
        src = k * ha
        out_pos = int(round(k * hs))
        seg = samples[src:src + frame]
        if len(seg) < frame:
            seg = array('f', seg) + array('f', [0.0]) * (frame - len(seg))
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


def ref_period_sync_stretch(samples, tempo, f0, sr):
    """参考实现：按真实基频周期，在正向过零处整段删除/复制周期。

    与 WSOLA 完全不同的实现路径：所有拼接点都在同相位（正向过零）位置，
    所以不产生相位跳变、基频保持不变、时长按 tempo 反比变化。
    适用于近似周期信号（与自测信号一致）。
    """
    zc = [0]
    for i in range(1, len(samples)):
        if samples[i - 1] <= 0.0 < samples[i]:
            zc.append(i)
    zc.append(len(samples))
    n_per = len(zc) - 2
    if n_per < 2:
        return array('f', samples)
    n_out_per = int(round(n_per / tempo))
    out = array('f')
    out.extend(samples[:zc[1]])
    last_p = -1
    for k in range(n_out_per):
        p = int(round(k * tempo))
        if p >= n_per:
            break
        if p == last_p:
            out.extend(samples[zc[p + 1]:zc[p + 2]])
        else:
            out.extend(samples[zc[p + 1]:zc[p + 2]])
        last_p = p
    out.extend(samples[zc[n_per + 1]:])
    return out
