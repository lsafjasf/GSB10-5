"""audiots - 纯标准库音频变速 / 变调库 (Python 3, stdlib only)

原理
----
* 变速 (tempo):  WSOLA (Waveform Similarity Overlap-Add)。
  只在时间轴上重排波形片段并做 Hann 窗重叠相加, 不重采样,
  因此频谱包络与基频不变, 仅时长变化。
* 变调 (pitch):  带抗混叠的 Hann 窗 sinc 重采样。
  重采样把所有频率等比缩放 (时长随之反比变化), 不改变相对谐波结构。
* 组合:          shift_tempo_pitch = 重采样(r) 之后接 WSOLA(t*r)。
  时长: n -> n/r -> n/r * t*r = n*t;  基频: f0 -> r*f0 -> r*f0 (WSOLA 透明)。
  两者机制正交: WSOLA 是时间轴操作 (pitch-transparent),
  重采样是频率轴操作 (且其时长副作用已被 WSOLA 精确补偿), 故互不串扰。

仅支持 16-bit PCM 单声道 WAV (读入时立体声自动混为单声道)。
"""

import math
import struct
import wave
from array import array

__all__ = [
    "read_wav", "write_wav", "resample", "time_stretch",
    "pitch_shift", "shift_tempo_pitch", "semitones_to_ratio",
]

MUL = __import__("operator").mul


# ---------------------------------------------------------------- WAV I/O

def read_wav(path):
    """返回 (samples: array('d') 取值 [-1,1], sample_rate). 多声道混成单声道."""
    with wave.open(path, "rb") as w:
        nch, sw, sr, nfr = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(nfr)
    if sw != 2:
        raise ValueError("仅支持 16-bit PCM WAV (得到 sampwidth=%d)" % sw)
    pcm = array("h")
    pcm.frombytes(raw)
    if pcm.itemsize != 2:
        pcm = array("h", struct.unpack("<%dh" % (len(raw) // 2), raw))
    if nch == 1:
        return array("d", (v / 32768.0 for v in pcm)), sr
    mono = array("d", [0.0]) * (len(pcm) // nch)
    inv = 1.0 / (32768.0 * nch)
    for c in range(nch):
        ch = pcm[c::nch]
        for i, v in enumerate(ch):
            mono[i] += v * inv
    return mono, sr


def write_wav(path, samples, sr):
    """samples: 可迭代 float [-1,1], 写成 16-bit PCM 单声道 WAV."""
    pcm = array("h", (max(-32768, min(32767, int(round(v * 32767.0)))) for v in samples))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


# ---------------------------------------------------------------- 重采样 (变调核心)

def resample(x, r, taps=16):
    """带抗混叠的重采样: 输出长度 round(len(x)/r), 频率整体乘 r。

    r > 1: 播放变快、音高乘 r、时长除 r (内部以截止 1/r 的低通抗混叠)。
    r < 1: 播放变慢、音高乘 r、时长除 r。
    核: h(u) = sinc(u/r') * hann(u / (r'*taps)), r' = max(r, 1), 每个输出点归一化。
    """
    n = len(x)
    if n == 0:
        return array("d")
    m = int(round(n / r))
    if m <= 0:
        return array("d")
    rp = r if r > 1.0 else 1.0          # 抗混叠: 下采样时收窄带宽
    half = rp * taps                     # 核半径 (输入样本数)
    # 预计算一个单位核表, 用线性插值查表以降低开销
    table_n = 4096
    scale = math.pi / rp
    tbl = []
    for i in range(table_n + 1):
        u = half * i / table_n
        if u == 0.0:
            s = 1.0
        else:
            s = math.sin(scale * u) / (scale * u)
        w = 0.5 + 0.5 * math.cos(math.pi * u / half)   # Hann
        tbl.append(s * w)
    inv_half_tbl = table_n / half

    out = array("d", [0.0]) * m
    xv = memoryview(x)
    last = n - 1
    for j in range(m):
        pos = j * r
        i0 = int(pos)
        lo = i0 - int(half) - 1
        hi = i0 + int(half) + 2
        if lo < 0:
            lo = 0
        if hi > n:
            hi = n
        acc = 0.0
        wsum = 0.0
        for i in range(lo, hi):
            u = pos - i
            au = -u if u < 0.0 else u
            k = au * inv_half_tbl
            ki = int(k)
            if ki >= table_n:
                continue
            frac = k - ki
            wgt = tbl[ki] + (tbl[ki + 1] - tbl[ki]) * frac
            acc += xv[i] * wgt
            wsum += wgt
        if wsum != 0.0:
            out[j] = acc / wsum
    return out


# ---------------------------------------------------------------- WSOLA (变速核心)

def _hann(n):
    if n % 2:
        return [0.5 - 0.5 * math.cos(2.0 * math.pi * i / (n - 1)) for i in range(n)]
    return [0.5 - 0.5 * math.cos(2.0 * math.pi * (i + 0.5) / n) for i in range(n)]


def time_stretch(x, rate, sr, win_ms=64.0, max_shift_ms=8.0):
    """WSOLA 变速不变调: rate>1 变慢变长, 输出长度 round(len(x)*rate)。

    合成位移 Hs = N/4 固定, 分析位移 Ha = Hs/rate; 每帧在
    [-max_shift, +max_shift] 内用归一化互相关 (粗搜 step4 + 细搜)
    寻找与已合成尾部波形最相似的对齐偏移, 保证拼接处相位连续。
    """
    n = len(x)
    if n == 0 or rate == 1.0:
        return array("d", x)
    n_out = int(round(n * rate))
    n_fft = 1
    while n_fft < int(sr * win_ms / 1000.0):
        n_fft <<= 1
    hs = n_fft // 4                       # 合成位移 (75% 重叠)
    overlap = n_fft - hs
    ha = hs / rate                        # 分析位移
    delta_max = int(sr * max_shift_ms / 1000.0)

    pad = n_fft + delta_max + 2
    xp = array("d", [0.0]) * pad + x + array("d", [0.0]) * pad
    xv = memoryview(xp)
    win = _hann(n_fft)

    cap = n_out + n_fft + 2 * hs
    out = array("d", [0.0]) * cap
    norm = array("d", [0.0]) * cap
    ov = memoryview(out)

    tau = 0.0
    wpos = 0
    eps = 1e-12
    mul = MUL
    while wpos + n_fft <= cap:
        center = int(round(tau)) + pad
        # --- 粗搜: 步长 4, 抽取 1/4 样本的相关 ---
        ref = ov[wpos:wpos + overlap:4]
        ref_e = sum(map(mul, ref, ref))
        best_d = 0
        best_c = -1.0
        if ref_e > eps:
            d = -delta_max
            while d <= delta_max:
                cand = xv[center + d: center + d + overlap:4]
                num = sum(map(mul, ref, cand))
                if num > best_c:
                    best_c = num
                    best_d = d
                d += 4
            # --- 细搜: 粗优附近全分辨率, 归一化互相关 ---
            ref_full = ov[wpos:wpos + overlap]
            ref_full_e = sum(map(mul, ref_full, ref_full))
            best_nc = -2.0
            bd = best_d
            lo = best_d - 3
            hi = best_d + 4
            if ref_full_e <= eps:
                ref_full_e = eps
            for d in range(lo, hi):
                cand = xv[center + d: center + d + overlap]
                num = sum(map(mul, ref_full, cand))
                den = sum(map(mul, cand, cand))
                nc = num / math.sqrt(den * ref_full_e + eps)
                if nc > best_nc:
                    best_nc = nc
                    bd = d
            best_d = bd
        src = center + best_d
        # --- 加窗重叠相加 + 归一化缓冲 ---
        seg = xv[src:src + n_fft]
        o = wpos
        for i in range(n_fft):
            w = win[i]
            out[o + i] += w * seg[i]
            norm[o + i] += w
        wpos += hs
        tau += ha
        if tau > n + pad - n_fft - delta_max - 2:
            break

    # 归一化 (Hann 75% 重叠的 COLA 和本就近似常数, 这里精确化并保护静音段)
    for i in range(cap):
        nv = norm[i]
        if nv > 1e-8:
            out[i] /= nv
    return out[hs:hs + n_out]


# ---------------------------------------------------------------- 组合接口

def semitones_to_ratio(semitones):
    return 2.0 ** (semitones / 12.0)


def pitch_shift(x, semitones, sr):
    """只变调不变速: 重采样(r) 改变音高(时长变 n/r), 再 WSOLA(r) 把时长补回。"""
    r = semitones_to_ratio(semitones)
    return time_stretch(resample(x, r), r, sr)


def shift_tempo_pitch(x, sr, tempo=1.0, semitones=0.0):
    """变速 + 变调, 独立控制。

    tempo:     时长倍率 (>1 更慢更长), 不影响音高
    semitones: 音高变化 (半音, 正为升调), 不影响时长
    实现: 先重采样 r=2^(st/12) (音高乘 r, 长度除 r),
          再 WSOLA(tempo*r) (长度乘 tempo*r, 音高不变)。
    """
    r = semitones_to_ratio(semitones)
    if r != 1.0:
        x = resample(x, r)
    total = tempo * r
    if total != 1.0:
        x = time_stretch(x, total, sr)
    return x


# ---------------------------------------------------------------- CLI

def _main(argv):
    import argparse
    p = argparse.ArgumentParser(description="纯标准库音频变速/变调")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--tempo", type=float, default=1.0, help="时长倍率, >1 更慢 (默认 1.0)")
    p.add_argument("--pitch", type=float, default=0.0, help="音高变化, 半音为单位 (默认 0)")
    a = p.parse_args(argv)
    x, sr = read_wav(a.input)
    y = shift_tempo_pitch(x, sr, tempo=a.tempo, semitones=a.pitch)
    write_wav(a.output, y, sr)
    print("in=%d out=%d (期望 %d) sr=%d" % (len(x), len(y), round(len(x) * a.tempo), sr))


if __name__ == "__main__":
    import sys
    _main(sys.argv[1:])
