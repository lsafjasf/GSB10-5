"""自测：小幅/大幅变速、只变调、组合、首尾静音、对拍、咔哒检测、5 分钟性能。

运行：python3 selftest.py
"""
import gc
import math
import resource
import time
from array import array

from audiostretch import (process, time_stretch, pitch_shift, resample,
                          estimate_f0, detect_clicks, content_duration, rms,
                          cents)
from audiostretch.refs import ref_resample_linear, ref_period_sync_stretch

SR = 44100
F0 = 220.0
NHARM = 6
PEAK = 0.5
MAX_FREQ = F0 * NHARM * 1.05


def gen_tone(f0, dur, sr=SR, nharm=NHARM, peak=PEAK):
    """谐波复合音，固定基频，带 10ms 起落包络避免自身咔哒。"""
    n = int(dur * sr)
    norm = sum(1.0 / k for k in range(1, nharm + 1))
    amp = peak / norm
    out = array('f', [0.0]) * n
    w = [2.0 * math.pi * f0 * k / sr for k in range(1, nharm + 1)]
    a = [amp / k for k in range(1, nharm + 1)]
    for i in range(n):
        s = 0.0
        for j in range(nharm):
            s += a[j] * math.sin(w[j] * i)
        out[i] = s
    fade = int(0.01 * sr)
    for i in range(fade):
        g = i / fade
        out[i] *= g
        out[n - 1 - i] *= g
    return out


def gen_perf_signal(dur=300.0, sr=SR):
    """5 分钟测试信号：音符序列 + 首尾 2 秒静音。"""
    notes = [110.0, 146.83, 164.81, 196.0, 220.0, 261.63]
    block = array('f')
    note_len = int(0.5 * sr)
    nharm = 5
    for rep in range(60):
        f0 = notes[rep % len(notes)]
        norm = sum(1.0 / k for k in range(1, nharm + 1))
        for i in range(note_len):
            env = math.exp(-2.0 * i / note_len)
            s = 0.0
            for k in range(1, nharm + 1):
                s += (1.0 / k) * math.sin(2.0 * math.pi * f0 * k * i / sr)
            block.append(0.4 / norm * env * s)
    silence = array('f', [0.0]) * (2 * sr)
    sig = silence + block + silence
    need = int(dur * sr)
    if len(sig) < need:
        sig = sig + array('f', [0.0]) * (need - len(sig))
    return sig[:need]


def fmt(x, nd=4):
    return ('%%.%df' % nd) % x


def measure_case(name, x, y, tempo, pitch, f0_in, sr=SR, hop_frame=2048):
    """对一次处理做全套测量并打印。"""
    n_in, n_out = len(x), len(y)
    exp_len = round(n_in / tempo)
    dur_err_ms = (n_out - exp_len) / sr * 1000.0
    f0_out, conf = estimate_f0(y, sr)
    exp_f0 = f0_in * pitch
    cerr = cents(f0_out, exp_f0)
    hop = (hop_frame // 2) / max(tempo, 1e-9) / pitch if pitch else 0
    clk = detect_clicks(y, sr, hop=hop, max_freq=MAX_FREQ * max(pitch, 1.0),
                        peak=PEAK)
    print('  时长: 输入 %d 采样 (%.3f s) -> 输出 %d 采样 (%.3f s), '
          '期望 %d, 误差 %+.3f ms' %
          (n_in, n_in / sr, n_out, n_out / sr, exp_len, dur_err_ms))
    print('  基频: 输入 %.2f Hz -> 实测 %.2f Hz (置信度 %.3f), 期望 %.2f Hz, '
          '误差 %+.2f 音分' % (f0_in, f0_out, conf, exp_f0, cerr))
    print('  咔哒: 越限点 %d 个, 最大|Δ|=%.5f (理论斜率界 %.5f, 阈值 %.5f), '
          '周期性拼接咔哒: %s' %
          (clk['clicks'], clk['max_diff'], clk['slope_bound'], clk['threshold'],
           '是' if clk['periodic_at_hop'] else '否'))
    return {'dur_err_ms': dur_err_ms, 'f0_out': f0_out, 'cents': cerr,
            'clicks': clk['clicks'], 'periodic': clk['periodic_at_hop']}


def perf_mode(tempo, semitones):
    """独立进程跑 5 分钟性能用例，打印一行结果。"""
    sig = gen_perf_signal(300.0)
    gc.collect()
    rss0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    t0 = time.perf_counter()
    out = process(sig, tempo=tempo, pitch=2.0 ** (semitones / 12.0))
    dt = time.perf_counter() - t0
    rss1 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print('tempo=%.2f pitch=%+.0f半音: 输出 %.1f s, 耗时 %.2f s, '
          '峰值 RSS %.0f MB (基线 %.0f MB)'
          % (tempo, semitones, len(out) / SR, dt,
             rss1 / 1024.0, rss0 / 1024.0))


def main():
    t_all = time.perf_counter()
    print('=' * 72)
    print('自测开始 (sr=%d Hz, 测试音 f0=%.1f Hz, %d 次谐波)' % (SR, F0, NHARM))
    print('=' * 72)

    dur = 3.0
    x = gen_tone(F0, dur)
    f0_in, conf_in = estimate_f0(x, SR)
    print('源信号: %.1f s, 实测基频 %.3f Hz (置信度 %.3f)' % (dur, f0_in, conf_in))
    results = []

    cases = [
        ('小幅变速 tempo=1.06 (只变速)', dict(tempo=1.06, pitch=1.0)),
        ('大幅变速 tempo=1.80 (只变速)', dict(tempo=1.80, pitch=1.0)),
        ('大幅变速 tempo=0.55 (只变速)', dict(tempo=0.55, pitch=1.0)),
        ('只变调 +3 半音 (tempo=1)', dict(tempo=1.0, pitch=2 ** (3 / 12))),
        ('只变调 -5 半音 (tempo=1)', dict(tempo=1.0, pitch=2 ** (-5 / 12))),
        ('组合 tempo=1.25 且 +2 半音', dict(tempo=1.25, pitch=2 ** (2 / 12))),
    ]
    for name, kw in cases:
        print('-' * 72)
        print('[用例] %s' % name)
        y = process(x, **kw)
        r = measure_case(name, x, y, kw['tempo'], kw['pitch'], f0_in)
        r['name'] = name
        results.append(r)

    # 首尾静音
    print('-' * 72)
    print('[用例] 首尾静音 0.5s + 2.0s 音 + 0.5s 静音, tempo=1.30 且 -2 半音')
    sil = array('f', [0.0]) * int(0.5 * SR)
    xs = sil + gen_tone(F0, 2.0) + sil
    tempo, pitch = 1.30, 2 ** (-2 / 12)
    ys = process(xs, tempo=tempo, pitch=pitch)
    r = measure_case('silence', xs, ys, tempo, pitch, f0_in)
    head = rms(ys[:int(0.3 * SR)])
    tail = rms(ys[len(ys) - int(0.3 * SR):])
    cont = content_duration(ys, SR)
    exp_cont = 2.0 / tempo
    print('  静音保持: 首部 0.3s RMS=%.2e, 尾部 0.3s RMS=%.2e' % (head, tail))
    print('  内容时长: 实测 %.3f s, 期望 %.3f s, 误差 %+.1f ms'
          % (cont, exp_cont, (cont - exp_cont) * 1000))
    r['name'] = '首尾静音 tempo=1.30 -2半音'
    results.append(r)

    # 对拍 1：tempo==pitch 时，process 应等价于纯重采样（参考实现）
    print('=' * 72)
    print('[对拍 1] process(tempo=1.4, pitch=1.4) vs 参考实现 ref_resample_linear(x, 1.4)')
    t1 = 1.4
    y_ours = process(x, tempo=t1, pitch=t1)
    y_ref = ref_resample_linear(x, t1)
    m = min(len(y_ours), len(y_ref))
    best_lag, best_corr = 0, -2.0
    for lag in range(-8, 9):
        num = sum(y_ours[i] * y_ref[i + lag] for i in range(1000, m - 1000))
        d1 = math.sqrt(sum(v * v for v in y_ours[1000:m - 1000]))
        d2 = math.sqrt(sum(y_ref[i + lag] ** 2 for i in range(1000, m - 1000)))
        c = num / (d1 * d2)
        if c > best_corr:
            best_corr, best_lag = c, lag
    f_ours, _ = estimate_f0(y_ours, SR)
    f_ref, _ = estimate_f0(y_ref, SR)
    print('  波形互相关系数 %.5f (最佳滞后 %+d 采样)' % (best_corr, best_lag))
    print('  基频: 本实现 %.2f Hz vs 参考 %.2f Hz, 差 %.2f 音分'
          % (f_ours, f_ref, cents(f_ours, f_ref)))
    print('  时长: 本实现 %d 采样 vs 参考 %d 采样, 差 %d 采样 (%.3f ms)'
          % (len(y_ours), len(y_ref), len(y_ours) - len(y_ref),
             (len(y_ours) - len(y_ref)) / SR * 1000))

    # 对拍 2：只变速 vs 过零周期同步增删参考（独立实现路径）
    print('-' * 72)
    print('[对拍 2] time_stretch(tempo=1.5) vs 参考实现 ref_period_sync_stretch')
    y_a = time_stretch(x, 1.5)
    y_b = ref_period_sync_stretch(x, 1.5, f0_in, SR)
    fa, _ = estimate_f0(y_a, SR)
    fb, _ = estimate_f0(y_b, SR)
    ca = detect_clicks(y_a, SR, max_freq=MAX_FREQ, peak=PEAK)
    cb = detect_clicks(y_b, SR, max_freq=MAX_FREQ, peak=PEAK)
    exp_len = round(len(x) / 1.5)
    print('  时长: 本实现 %d 采样 (期望 %d, 误差 %+.2f ms); 参考 %d 采样 (误差 %+.2f ms)'
          % (len(y_a), exp_len, (len(y_a) - exp_len) / SR * 1000,
             len(y_b), (len(y_b) - exp_len) / SR * 1000))
    print('  基频: 本实现 %.2f Hz vs 参考 %.2f Hz, 差 %.2f 音分'
          % (fa, fb, cents(fa, fb)))
    print('  咔哒越限点: 本实现(WSOLA) %d 个 vs 参考(周期同步) %d 个'
          % (ca['clicks'], cb['clicks']))

    # 往返一致性：1.5x 再 1/1.5x，按帧允许 ±frame/8 局部对齐的相对误差
    print('-' * 72)
    print('[一致性] 往返 tempo=1.5 后 tempo=1/1.5，逐帧对齐相对误差')
    y_rt = time_stretch(time_stretch(x, 1.5), 1 / 1.5)
    frame_rt = 2048
    errs = []
    for st in range(0, len(x) - frame_rt, frame_rt):
        ref = x[st:st + frame_rt]
        e_ref = sum(v * v for v in ref)
        if e_ref < 1e-6:
            continue
        best = None
        for d in range(-256, 257, 4):
            seg = y_rt[st + d:st + d + frame_rt]
            if len(seg) < frame_rt:
                continue
            e = sum((a - b) ** 2 for a, b in zip(ref, seg))
            if best is None or e < best:
                best = e
        errs.append(best / e_ref)
    errs.sort()
    print('  逐帧相对误差: 中位 %.2e, 95 分位 %.2e (长度 %d vs %d)'
          % (errs[len(errs) // 2], errs[int(len(errs) * 0.95)], len(x), len(y_rt)))

    # 性能：5 分钟（每个用例独立子进程，测干净的峰值 RSS）
    print('=' * 72)
    print('[性能] 5 分钟单声道 44.1kHz 信号（独立子进程测量）')
    import subprocess, sys
    for label, t_, p_ in [('只变速 tempo=1.25', 1.25, 0.0),
                          ('只变调 +2 半音', 1.0, 2.0),
                          ('组合 tempo=1.25 且 +2 半音', 1.25, 2.0)]:
        out = subprocess.run([sys.executable, 'selftest.py', '--perf',
                              str(t_), str(p_)],
                             capture_output=True, text=True, check=True)
        print('  ' + out.stdout.strip())

    # 汇总
    print('=' * 72)
    print('[汇总]')
    ok = True
    for r in results:
        flag = (abs(r['dur_err_ms']) < 1.0 and abs(r['cents']) < 30.0
                and r['clicks'] == 0 and not r['periodic'])
        ok = ok and flag
        print('  %-34s 时长误差 %+6.3f ms, 基频误差 %+6.2f 音分, 咔哒 %d -> %s'
              % (r['name'], r['dur_err_ms'], r['cents'], r['clicks'],
                 '通过' if flag else '未通过'))
    print('总体: %s (耗时 %.1f s)' % ('全部通过' if ok else '存在未通过项',
                                      time.perf_counter() - t_all))
    return 0 if ok else 1


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == '--perf':
        perf_mode(float(sys.argv[2]), float(sys.argv[3]))
    else:
        raise SystemExit(main())
