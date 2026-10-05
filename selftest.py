"""自测：覆盖重叠区很大 / 很小 / 曝光差异明显 / 接缝必经主体 四种情形。

运行：python3 selftest.py
输出：out/ 下的拼接结果 PGM、接缝路径 JSON、亮度差异 JSON。
"""

import json
import math
import os
import random

from seamstitch import (Image, stitch, find_seam, apply_gain, estimate_gain,
                        mean_abs_diff, write_pgm, SUBJECT_GRAD_THRESHOLD)

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
os.makedirs(OUT, exist_ok=True)

W, H = 160, 100


def scene_base(w=W, h=H, seed=0):
    """背景：平滑渐变 + 弱纹理（低对比内容）。"""
    rnd = random.Random(seed)
    img = Image(w, h)
    for y in range(h):
        for x in range(w):
            v = 90 + 40 * math.sin(x / 30.0) + 25 * math.cos(y / 22.0)
            v += rnd.uniform(-3, 3)
            img.pixels[y][x] = max(0, min(255, int(v)))
    return img


def add_subject(img, cx, cy, rw, rh, lo=20, hi=235):
    """画一个高对比竖条主体（黑边白条，梯度远超阈值）。"""
    for y in range(max(0, cy - rh), min(img.height, cy + rh)):
        for x in range(max(0, cx - rw), min(img.width, cx + rw)):
            img.pixels[y][x] = hi if (x - cx) % 6 < 3 else lo
    return img


def crop(img, x0, w):
    return Image(w, img.height, [row[x0:x0 + w] for row in img.pixels])


def scale(img, gain):
    return apply_gain(img, gain)


results = {}

# ---- 情形 1：重叠区很大（overlap=80，占图宽一半） ----
scene = scene_base()
add_subject(scene, cx=40, cy=50, rw=10, rh=40)   # 主体在非重叠区
a = crop(scene, 0, 100)
b = crop(scene, 20, 100)                            # dx=20 -> overlap=80
res, rep = stitch(a, b, 20)
write_pgm(res, os.path.join(OUT, "case1_large_overlap.pgm"))
results["case1_large_overlap"] = rep
assert rep["status"] == "seam" and rep["overlap_width"] == 80

# ---- 情形 2a：重叠区很小（overlap=5 < MIN_OVERLAP=8）-> 退路线性融合 ----
scene = scene_base(seed=1)
a = crop(scene, 0, 100)
b = crop(scene, 95, 65)                             # dx=95 -> overlap=5
res, rep = stitch(a, b, 95)
write_pgm(res, os.path.join(OUT, "case2_small_overlap_fallback.pgm"))
results["case2_small_overlap_fallback"] = rep
assert rep["status"] == "fallback_blend"

# ---- 情形 2b：无重叠 -> 拒绝拼接 ----
res, rep = stitch(a, b, 100)
results["case2b_no_overlap_rejected"] = rep
assert rep["status"] == "rejected" and res is None

# ---- 情形 3：曝光差异明显（B 整体暗 40%） ----
scene = scene_base(seed=2)
a = crop(scene, 0, 100)
b = scale(crop(scene, 40, 100), 0.6)                # dx=40 -> overlap=60
res, rep = stitch(a, b, 40)
write_pgm(res, os.path.join(OUT, "case3_exposure_diff.pgm"))
results["case3_exposure_diff"] = rep
assert rep["status"] == "seam"
assert rep["brightness_diff_after"] < rep["brightness_diff_before"] * 0.3, \
    "曝光补偿未显著降低亮度差"

# ---- 情形 4：接缝必经主体（主体竖条贯穿整个重叠区全高） ----
scene = scene_base(seed=3)
for y in range(30, 70):                             # 横向条纹主体带，横挡整个重叠区
    for x in range(60, 100):
        scene.pixels[y][x] = 235 if y % 6 < 3 else 20

a = crop(scene, 0, 100)
b = crop(scene, 60, 100)                            # dx=60 -> overlap=40
res, rep = stitch(a, b, 60)
write_pgm(res, os.path.join(OUT, "case4_forced_crossing.pgm"))
results["case4_forced_crossing"] = rep
assert rep["status"] == "seam"
st = rep["seam_stats"]
assert st["subject_crossings"] > 0, "必经主体时穿越次数应 > 0"
assert st["subject_crossings"] <= st["straight_crossings"], \
    "DP 接缝穿越次数不应多于直线接缝"

# ---- 附加：直线接缝 vs DP 接缝对比（主体位于重叠区中部但不填满） ----
scene = scene_base(seed=4)
add_subject(scene, cx=70, cy=50, rw=6, rh=45)       # 主体在重叠区中央
a = crop(scene, 0, 100)
b = crop(scene, 50, 100)                            # overlap=50，直线会穿主体
gain = estimate_gain(a, b, 50)
ac = apply_gain(a, gain)
path, st = find_seam(ac, b, 50)
res, rep = stitch(a, b, 50)
write_pgm(res, os.path.join(OUT, "case5_avoid_subject.pgm"))
results["case5_avoid_subject"] = rep
assert st["straight_crossings"] > 0
assert st["subject_crossings"] == 0, "DP 接缝应完全绕开主体"

# ---- 汇总输出 ----
for name, rep in results.items():
    if rep.get("seam_path"):
        rep["seam_path_head"] = rep["seam_path"][:10]  # 摘要便于查看
summary = {k: {kk: vv for kk, vv in v.items() if kk != "seam_path"}
           for k, v in results.items()}
with open(os.path.join(OUT, "selftest_report.json"), "w") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)
# 完整接缝路径单独存一份
paths = {k: v["seam_path"] for k, v in results.items() if v.get("seam_path")}
with open(os.path.join(OUT, "seam_paths.json"), "w") as f:
    json.dump(paths, f, indent=1)

print("全部断言通过。报告写入 out/selftest_report.json，接缝路径写入 out/seam_paths.json")
for k, v in summary.items():
    print(" ", k, "->", v.get("status"),
          "| 亮度差 %.2f -> %.2f" % (v.get("brightness_diff_before") or 0,
                                     v.get("brightness_diff_after") or 0)
          if v.get("brightness_diff_before") is not None else "")
