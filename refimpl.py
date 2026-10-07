"""refimpl - 独立参考实现, 用于与 audiots 对拍。

* ola_stretch:   教科书式定步长 OLA 变速 (Hann 窗, 无波形相似性搜索)。
* ref_resample:  标准库 C 实现 audioop.ratecv (线性插值) 的重采样参考。
两者与 audiots 的实现路径完全独立。
"""

import math
import warnings
from array import array


def ola_stretch(x, rate, sr, win_ms=64.0):
    """定步长 OLA 变速参考: 分析位移 Ha=N/4/rate, 合成位移 Hs=N/4, 不做对齐搜索。"""
    n = len(x)
    if n == 0 or rate == 1.0:
        return array("d", x)
    n_out = int(round(n * rate))
    n_fft = 1
    while n_fft < int(sr * win_ms / 1000.0):
        n_fft <<= 1
    hs = n_fft // 4
    ha = hs / rate
    win = [0.5 - 0.5 * math.cos(2.0 * math.pi * (i + 0.5) / n_fft) for i in range(n_fft)]
    cap = n_out + n_fft + 2 * hs
    out = array("d", [0.0]) * cap
    norm = array("d", [0.0]) * cap
    tau = 0.0
    wpos = 0
    while wpos + n_fft <= cap:
        c = int(round(tau))
        if c + n_fft > n:
            break
        for i in range(n_fft):
            w = win[i]
            out[wpos + i] += w * x[c + i]
            norm[wpos + i] += w
        wpos += hs
        tau += ha
    for i in range(cap):
        if norm[i] > 1e-8:
            out[i] /= norm[i]
    return out[hs:hs + n_out]


def ref_resample(x, r, sr):
    """audioop.ratecv (stdlib C 实现) 重采样参考: 输出长度 round(len(x)/r)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        import audioop
    n = len(x)
    m = int(round(n / r))
    if m <= 0:
        return array("d")
    pcm = array("h", (max(-32768, min(32767, int(round(v * 32767.0)))) for v in x))
    # ratecv 以 (in_rate, out_rate) 工作: 长度 m = n * out_rate/in_rate
    out_rate = int(round(sr / r))
    frag, _ = audioop.ratecv(pcm.tobytes(), 2, 1, sr, out_rate, None)
    y16 = array("h")
    y16.frombytes(frag)
    y = array("d", (v / 32768.0 for v in y16))
    if len(y) > m:
        y = y[:m]
    return y
