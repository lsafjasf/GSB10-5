"""selftest - audiots 自测: 时长/基频实测、咔哒检测、与参考实现对拍。

运行: python3 selftest.py
"""

import math
import time
from array import array
from operator import mul

import audiots
import refimpl

SR = 16000


# ------------------------------------------------------------ 测试信号

def harmonic_tone(f0=220.0, dur=2.0, sr=SR, nharm=5, amp=0.5):
    n = int(dur * sr)
    out = array("d", [0.0]) * n
    for h in range(1, nharm + 1):
        a = amp / h
        w = 2.0 * math.pi * f0 * h / sr
        for i in range(n):
            out[i] += a * math.sin(w * i)
    # 10ms 起止淡入淡出, 避免信号自身带"咔哒"
    fade = int(0.01 * sr)
    for i in range(fade):
        g = i / fade
        out[i] *= g
        out[n - 1 - i] *= g
    return out


def with_silence(x, sr=SR, pad_s=0.4):
    pad = array("d", [0.0]) * int(pad_s * sr)
    return pad + x + pad


# ------------------------------------------------------------ 基频估计 (归一化自相关 + 抛物线插值)

def estimate_f0(x, sr=SR, fmin=50.0, fmax=500.0, hop=800, frame=1600):
    """YIN 式基频估计: 归一化差分函数 + 首个低于阈值的谷 + 抛物线插值.
    返回 (中位 f0, f0 序列). 差分按 1/2 抽取以加速."""
    lag_lo = int(sr / fmax)
    lag_hi = min(int(sr / fmin), frame // 2 - 2)
    f0s = []
    pos = 0
    n = len(x)
    while pos + frame <= n:
        seg = x[pos:pos + frame:2]          # 1/2 抽取
        e0 = sum(map(mul, seg, seg))
        if e0 < 1e-6:
            pos += hop
            continue
        # 归一化差分 d'(lag) = 2 - 2*nc(lag), 取首个 <0.2 的谷, 避免八度错误
        prev = None
        best_lag = -1
        for lag in range(lag_lo, lag_hi + 1):
            cand = x[pos + lag: pos + lag + frame:2]
            num = sum(map(mul, seg, cand))
            den = sum(map(mul, cand, cand))
            d = 2.0 - 2.0 * num / math.sqrt(den * e0 + 1e-18)
            if d < 0.2:
                # 在低于阈值的区间内找最小
                best_lag, best_d = lag, d
                for lag2 in range(lag + 1, min(lag_hi, lag + lag // 2) + 1):
                    cand = x[pos + lag2: pos + lag2 + frame:2]
                    num = sum(map(mul, seg, cand))
                    den = sum(map(mul, cand, cand))
                    d2 = 2.0 - 2.0 * num / math.sqrt(den * e0 + 1e-18)
                    if d2 < best_d:
                        best_lag, best_d = lag2, d2
                    elif d2 > best_d + 0.05:
                        break
                break
        if best_lag > 0:
            # 抛物线插值 (原分辨率)
            def nc(lag):
                a = x[pos:pos + frame]
                b = x[pos + lag:pos + lag + frame]
                num = sum(map(mul, a, b))
                return num / math.sqrt(sum(map(mul, b, b)) * e0 * 4.0 + 1e-18)
            y0, y1, y2 = nc(best_lag - 1), nc(best_lag), nc(best_lag + 1)
            denom = (y0 - 2.0 * y1 + y2)
            shift = 0.5 * (y0 - y2) / denom if abs(denom) > 1e-12 else 0.0
            f0s.append(sr / (best_lag + shift))
        pos += hop
    if not f0s:
        return 0.0, []
    f0s_sorted = sorted(f0s)
    return f0s_sorted[len(f0s_sorted) // 2], f0s


# ------------------------------------------------------------ 咔哒检测 (二阶差分离群点)

def detect_clicks(x, peak_ref=None):
    """返回 (咔哒数, max|二阶差分|/峰值). 拼接不连续会在二阶差分上产生尖峰."""
    n = len(x)
    if n < 3:
        return 0, 0.0
    d = array("d", [0.0]) * (n - 2)
    for i in range(1, n - 1):
        d[i - 1] = abs(x[i + 1] - 2.0 * x[i] + x[i - 1])
    sd = sorted(d)
    med = sd[len(sd) // 2] or 1e-12
    thr = max(10.0 * 1.4826 * med, 1e-9)
    peak = peak_ref if peak_ref else max(abs(v) for v in x) or 1.0
    count = 0
    mx = 0.0
    for v in d:
        if v > thr:
            count += 1
        if v > mx:
            mx = v
    return count, mx / peak


# ------------------------------------------------------------ 对比工具

def ncc(a, b):
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    num = sum(map(mul, a, b))
    den = math.sqrt(sum(map(mul, a, a)) * sum(map(mul, b, b)) + 1e-18)
    return num / den


def envelope_ncc(a, b, sr=SR, win=160):
    """10ms 能量包络的相关性: 对相位不敏感, 衡量时间/幅度结构是否一致."""
    def env(x):
        return [math.sqrt(sum(v * v for v in x[i:i + win]) / win)
                for i in range(0, len(x) - win, win)]
    return ncc(array("d", env(a)), array("d", env(b)))


def snr_db(ref, tst):
    n = min(len(ref), len(tst))
    e_ref = sum(v * v for v in ref[:n])
    e_err = sum((ref[i] - tst[i]) ** 2 for i in range(n))
    return 10.0 * math.log10(e_ref / (e_err + 1e-18) + 1e-18)


def cents(f1, f2):
    return 1200.0 * math.log2(f2 / f1) if f1 > 0 and f2 > 0 else float("nan")


def fmt(v, nd=2):
    return ("%.*f" % (nd, v)) if isinstance(v, float) else str(v)


# ------------------------------------------------------------ 测试流程

ROWS = []


def row(case, target_dur, got_dur, target_f0, got_f0, clicks, click_ratio, extra=""):
    ROWS.append((case, target_dur, got_dur, target_f0, got_f0, clicks, click_ratio, extra))


def run_case(name, x, y, expect_tempo, expect_ratio, f0_src, check_silence=False):
    dur_t = len(x) * expect_tempo / SR
    dur_g = len(y) / SR
    f0_t = f0_src * expect_ratio
    f0_g, _ = estimate_f0(y, fmax=max(500.0, f0_t * 1.3))
    c, cr = detect_clicks(y)
    extra = "时长误差 %.3f%%" % (100.0 * (dur_g - dur_t) / dur_t)
    extra += ", 基频误差 %+.1f 音分" % cents(f0_t, f0_g)
    if check_silence:
        head = max(abs(v) for v in y[: int(0.3 * SR)])
        tail = max(abs(v) for v in y[len(y) - int(0.3 * SR):])
        extra += ", 首/尾静音峰值 %.1e/%.1e" % (head, tail)
    row(name, dur_t, dur_g, f0_t, f0_g, c, cr, extra)
    return y


def main():
    t_all = time.perf_counter()
    src = harmonic_tone()
    f0_src, _ = estimate_f0(src)
    print("# 源信号: 谐波音 f0 实测 %.2f Hz (理论 220), 时长 %.3f s, sr=%d" % (f0_src, len(src) / SR, SR))
    print()

    # ---- 恒等 ----
    y = audiots.shift_tempo_pitch(src, SR, 1.0, 0.0)
    err = max(abs(a - b) for a, b in zip(src, y))
    print("# 恒等 (tempo=1, pitch=0): 最大逐样本误差 %.3e" % err)
    print()

    # ---- 小幅变速 ----
    run_case("小幅变速 x1.06", src, audiots.shift_tempo_pitch(src, SR, 1.06, 0.0), 1.06, 1.0, f0_src)
    run_case("小幅变速 x0.94", src, audiots.shift_tempo_pitch(src, SR, 0.94, 0.0), 0.94, 1.0, f0_src)
    # ---- 大幅变速 ----
    run_case("大幅变速 x2.00", src, audiots.shift_tempo_pitch(src, SR, 2.00, 0.0), 2.00, 1.0, f0_src)
    run_case("大幅变速 x0.50", src, audiots.shift_tempo_pitch(src, SR, 0.50, 0.0), 0.50, 1.0, f0_src)
    # ---- 只变调 ----
    for st in (+2, -3, +7):
        run_case("只变调 %+d 半音" % st, src, audiots.shift_tempo_pitch(src, SR, 1.0, st),
                 1.0, audiots.semitones_to_ratio(st), f0_src)
    # ---- 首尾静音 ----
    xs = with_silence(src)
    run_case("首尾静音+变速x1.3+升2半音", xs, audiots.shift_tempo_pitch(xs, SR, 1.3, 2.0),
             1.3, audiots.semitones_to_ratio(2.0), f0_src, check_silence=True)
    # ---- 组合 ----
    run_case("组合 x1.25 & -2半音", src, audiots.shift_tempo_pitch(src, SR, 1.25, -2.0),
             1.25, audiots.semitones_to_ratio(-2.0), f0_src)
    # ---- 纯正弦 (拼接相位最敏感) ----
    n = 2 * SR
    sine = array("d", [0.6 * math.sin(2.0 * math.pi * 440.0 * i / SR) for i in range(n)])
    f0_sine, _ = estimate_f0(sine)
    run_case("纯音440Hz 变速x1.7", sine, audiots.shift_tempo_pitch(sine, SR, 1.7, 0.0),
             1.7, 1.0, f0_sine)
    run_case("纯音440Hz 升5半音", sine, audiots.shift_tempo_pitch(sine, SR, 1.0, 5.0),
             1.0, audiots.semitones_to_ratio(5.0), f0_sine)

    print("## 功能测试 (时长 / 基频 / 咔哒)")
    print()
    print("| 用例 | 目标时长s | 实测时长s | 目标f0 Hz | 实测f0 Hz | 咔哒数 | 咔哒峰值比 | 备注 |")
    print("|---|---|---|---|---|---|---|---|")
    for r in ROWS:
        print("| %s | %.3f | %.3f | %.2f | %.2f | %d | %.4f | %s |" % r)
    print()

    # ================= 对拍 =================
    print("## 与参考实现对拍")
    print()

    # R1: 重采样器 vs audioop.ratecv
    print("### R1 重采样器 vs 标准库 audioop.ratecv (C 实现)")
    print()
    def truth_resampled(r):
        m = int(round(len(src) / r))
        out = array("d", [0.0]) * m
        for h in range(1, 6):
            a = 0.5 / h
            w = 2.0 * math.pi * 220.0 * h * r / SR
            for j in range(m):
                out[j] += a * math.sin(w * j)
        fade = int(0.01 * SR / r)
        for i in range(fade):
            g = i / fade
            out[i] *= g
            out[len(out) - 1 - i] *= g
        return out

    print("| 倍率 r | 长度(本库/参考) | 本库 vs 解析真值 SNR | 参考 vs 解析真值 SNR | 本库 vs 参考 SNR |")
    print("|---|---|---|---|---|")
    for r in (1.5, 0.7):
        a = audiots.resample(src, r)
        b = refimpl.ref_resample(src, r, SR)
        t = truth_resampled(r)
        edge = 300
        snr_a = snr_db(t[edge:len(t) - edge], a[edge:len(a) - edge])
        snr_b = snr_db(t[edge:len(t) - edge], b[edge:min(len(b), len(t)) - edge])
        nn = min(len(a), len(b))
        print("| %.2f | %d / %d | %.1f dB | %.1f dB | %.1f dB |"
              % (r, len(a), len(b), snr_a, snr_b, snr_db(b[edge:nn - edge], a[edge:nn - edge])))
    print()

    # R2: 变速 vs 定步长 OLA 参考
    print("### R2 WSOLA 变速 vs 定步长 OLA 参考 (refimpl.ola_stretch)")
    print()
    print("| 倍率 | 时长差(样本) | f0误差音分(本库/参考) | 波形NCC | 包络NCC | 咔哒(本库/参考) |")
    print("|---|---|---|---|---|---|")
    for rate in (1.06, 1.5, 2.0, 0.5):
        a = audiots.time_stretch(src, rate, SR)
        b = refimpl.ola_stretch(src, rate, SR)
        fa, _ = estimate_f0(a)
        fb, _ = estimate_f0(b)
        ca, _ = detect_clicks(a)
        cb, _ = detect_clicks(b)
        print("| %.2f | %d | %+.1f / %+.1f | %.4f | %.4f | %d / %d |"
              % (rate, abs(len(a) - len(b)), cents(f0_src, fa), cents(f0_src, fb),
                 ncc(a, b), envelope_ncc(a, b), ca, cb))
    print()

    # R3: 变调 vs 纯重采样参考 (audioop)
    print("### R3 变调 vs 纯重采样参考 (参考只变调但时长随之变化)")
    print()
    print("| 半音 | 目标 f0 Hz | 本库 f0 Hz | 参考 f0 Hz | f0 差(音分) | 本库时长s | 参考时长s |")
    print("|---|---|---|---|---|---|---|")
    for st in (+2, -3, +7):
        r = audiots.semitones_to_ratio(st)
        a = audiots.pitch_shift(src, st, SR)
        b = refimpl.ref_resample(src, r, SR)
        fa, _ = estimate_f0(a)
        fb, _ = estimate_f0(b)
        print("| %+d | %.2f | %.2f | %.2f | %+.1f | %.3f | %.3f |"
              % (st, f0_src * r, fa, fb, cents(fb, fa), len(a) / SR, len(b) / SR))
    print()
    print("总耗时 %.1f s" % (time.perf_counter() - t_all))


if __name__ == "__main__":
    main()
