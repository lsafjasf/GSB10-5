"""自测：在带人工标注的合成视频上运行检测器，输出漏检/误检/密度。

运行：python3 selftest.py [--debug]
"""

import sys

from keyframe import KeyframeDetector
from keyframe.metrics import FrameFeatures, pair_metrics
from keyframe.synthetic import build_test_clip, WIDTH, HEIGHT, FPS

CUT_TOL = 2          # 硬切换允许的帧号误差
GRAD_MIN_IOU = 0.3   # 渐变区间匹配的最小交并比


def overlap(a, b):
    lo = max(a[0], b[0])
    hi = min(a[1], b[1])
    return max(0, hi - lo)


def iou(a, b):
    inter = overlap(a, b)
    union = (a[1] - a[0]) + (b[1] - b[0]) - inter
    return inter / union if union else 0.0


def evaluate(events, gt, total_frames):
    cuts = [e for e in events if e.kind == "cut"]
    grads = [e for e in events if e.kind == "gradual"]

    # --- 硬切换 ---
    matched_gt, matched_pred = set(), set()
    for gi, g in enumerate(gt["cuts"]):
        for pi, p in enumerate(cuts):
            if pi in matched_pred:
                continue
            if abs(p.index - g) <= CUT_TOL:
                matched_gt.add(gi)
                matched_pred.add(pi)
                break
    cut_missed = [gt["cuts"][i] for i in range(len(gt["cuts"]))
                  if i not in matched_gt]
    cut_false = [p.index for i, p in enumerate(cuts)
                 if i not in matched_pred]

    # --- 渐变转场 ---
    grad_ranges = [(p.index - p.duration, p.index) for p in grads]
    g_matched_gt, g_matched_pred = set(), set()
    for gi, g in enumerate(gt["graduals"]):
        for pi, p in enumerate(grad_ranges):
            if pi in g_matched_pred:
                continue
            if iou(g, p) >= GRAD_MIN_IOU or overlap(g, p) >= 0.5 * (g[1] - g[0]):
                g_matched_gt.add(gi)
                g_matched_pred.add(pi)
                break
    grad_missed = [gt["graduals"][i] for i in range(len(gt["graduals"]))
                   if i not in g_matched_gt]
    grad_false = [grad_ranges[i] for i in range(len(grad_ranges))
                  if i not in g_matched_pred]

    # --- 误检到不该产关键帧的区间 ---
    # 真实边界帧本身落在标注的静态区间起点，需要排除“允许区域”：
    # 标注硬切换前后 CUT_TOL 帧、标注渐变区间前后各 2 帧。
    allowed = set()
    for g in gt["cuts"]:
        allowed.update(range(g - CUT_TOL, g + CUT_TOL + 1))
    for gs, ge in gt["graduals"]:
        allowed.update(range(gs - 2, ge + 3))

    def in_ranges(idx, ranges):
        return any(s <= idx < e for s, e in ranges)

    bad_zone_hits = []
    for e in events:
        if e.kind not in ("cut", "gradual"):
            continue
        if e.index in allowed:
            continue
        if in_ranges(e.index, gt["motion"]):
            bad_zone_hits.append(("motion", e.kind, e.index))
        if in_ranges(e.index, gt["statics"]):
            bad_zone_hits.append(("static", e.kind, e.index))
        if e.index in gt["flashes"]:
            bad_zone_hits.append(("flash", e.kind, e.index))

    # --- 密度 ---
    n_keys = len(cuts) + len(grads)
    density_per_min = n_keys / (total_frames / FPS / 60)
    longest_static = max(gt["statics"], key=lambda r: r[1] - r[0])
    ls, le = longest_static
    # 区间两端各内缩 3 帧（避开相邻转场的收尾事件），只数纯静态内部
    static_keys = sum(1 for e in events
                      if e.kind in ("cut", "gradual")
                      and ls + 3 <= e.index < le - 3)
    return {
        "cut_missed": cut_missed, "cut_false": cut_false,
        "grad_missed": grad_missed, "grad_false": grad_false,
        "bad_zone_hits": bad_zone_hits,
        "n_keyframes": n_keys,
        "density_per_min": density_per_min,
        "longest_static_frames": longest_static[1] - longest_static[0],
        "keys_in_long_static": static_keys,
    }


def debug_metrics(frames, gt):
    """打印每个标注区间内的度量范围，用于阈值标定。"""
    feats = [FrameFeatures(f) for f in frames]
    print("== 各区间的相邻帧度量 (chi2, frac, dmean) ==")
    zones = ([("static", r) for r in gt["statics"]]
             + [("motion", r) for r in gt["motion"]]
             + [("gradual", r) for r in gt["graduals"]])
    for kind, (s, e) in zones:
        cs, fs, ds = [], [], []
        for i in range(max(1, s), e):
            c, f, d = pair_metrics(feats[i - 1], feats[i],
                                   frames[i - 1], frames[i], WIDTH, HEIGHT)
            cs.append(c); fs.append(f); ds.append(d)
        if not cs:
            continue
        print(f"{kind:8s} [{s:4d},{e:4d}) "
              f"chi2 {min(cs):.3f}~{max(cs):.3f}  "
              f"frac {min(fs):.3f}~{max(fs):.3f}  "
              f"|dmean| {min(abs(d) for d in ds):.2f}~{max(abs(d) for d in ds):.2f}")
    print("== 硬切换边界帧 ==")
    for g in gt["cuts"]:
        c, f, d = pair_metrics(feats[g - 1], feats[g],
                               frames[g - 1], frames[g], WIDTH, HEIGHT)
        print(f"cut@{g}: chi2={c:.3f} frac={f:.3f} dmean={d:.2f}")
    for fl in gt["flashes"]:
        c, f, d = pair_metrics(feats[fl - 1], feats[fl],
                               frames[fl - 1], frames[fl], WIDTH, HEIGHT)
        print(f"flash@{fl}: chi2={c:.3f} frac={f:.3f} dmean={d:.2f}")


def main():
    debug = "--debug" in sys.argv
    frames, gt = build_test_clip()
    if debug:
        debug_metrics(frames, gt)

    det = KeyframeDetector(WIDTH, HEIGHT)
    for f in frames:
        det.feed(f)
    events = det.finish()

    print(f"\n总帧数 {len(frames)}（{len(frames)/FPS:.1f}s @ {FPS}fps）")
    print("检出事件：")
    for e in events:
        if e.kind in ("cut", "gradual"):
            print(f"  {e.kind:8s} @ {e.index:4d}  "
                  f"(chi2={e.metrics[0]:.3f}, dmean={e.metrics[2]:.1f}, "
                  f"dur={e.duration})")

    r = evaluate(events, gt, len(frames))
    print("\n== 与人工标注对比 ==")
    print(f"硬切换：标注 {len(gt['cuts'])} 个，"
          f"漏检 {len(r['cut_missed'])} {r['cut_missed']}，"
          f"误检 {len(r['cut_false'])} {r['cut_false']}")
    print(f"渐变转场：标注 {len(gt['graduals'])} 个，"
          f"漏检 {len(r['grad_missed'])} {r['grad_missed']}，"
          f"误检 {len(r['grad_false'])} {r['grad_false']}")
    print(f"运动/静态/闪光灯区间内的误检关键帧：{len(r['bad_zone_hits'])} "
          f"{r['bad_zone_hits']}")
    print("\n== 抽帧密度 ==")
    print(f"关键帧总数 {r['n_keyframes']}，"
          f"密度 {r['density_per_min']:.2f} 个/分钟")
    print(f"最长静态镜头 {r['longest_static_frames']} 帧"
          f"（{r['longest_static_frames']/FPS:.1f}s），"
          f"内部误产关键帧 {r['keys_in_long_static']} 个")

    ok = (not r["cut_missed"] and not r["cut_false"]
          and not r["grad_missed"] and not r["grad_false"]
          and not r["bad_zone_hits"]
          and r["keys_in_long_static"] == 0)
    print("\n结果:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
