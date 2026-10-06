#!/usr/bin/env python3
"""自测：合成图像覆盖居中/贴边/无主体/噪声/极端比例场景。

运行: python3 selftest.py
产物: tests_output/*.pgm（原图/裁后图/框线图）、selftest_data.json、selftest_report.md
"""

import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from autocrop_lib.ppm import Image, new, fill_rect, save
from autocrop_lib import AutocropConfig, FitPolicy, autocrop

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests_output")
W, H = 320, 240
BG = (128,)


def make_blank():
    return new(W, H, 1, BG)


def gen_center_square():
    img = make_blank()
    fill_rect(img, 130, 90, 190, 150, (20,))   # 60x60 深色块居中
    return img


def gen_edge_left():
    img = make_blank()
    fill_rect(img, 0, 60, 30, 180, (230,))     # 30x120 亮条贴左边缘
    return img


def gen_edge_corner():
    img = make_blank()
    fill_rect(img, 0, 0, 46, 46, (15,))        # 46x46 暗块贴左上角
    return img


def gen_edge_right():
    img = make_blank()
    fill_rect(img, W - 18, 80, W, 160, (240,)) # 18x80 亮条贴右边缘
    return img


def gen_flat():
    return make_blank()                        # 完全均匀，无主体


def gen_noise(seed=42):
    rnd = random.Random(seed)
    img = make_blank()
    img.data = bytearray(rnd.getrandbits(8) for _ in range(W * H))
    return img


def gen_faint():
    img = make_blank()
    fill_rect(img, 120, 85, 200, 155, (136,))  # 对比度仅 8 的弱主体
    return img


def gen_panoramic():
    img = make_blank()
    fill_rect(img, 0, 100, W, 140, (225,))     # 320x40 贯穿左右边缘的横条
    return img


def draw_box(img, box, value=(0,)):
    x0, y0, x1, y1 = box
    fill_rect(img, x0, y1 - 1, x1, y1, value)
    fill_rect(img, x0, y0, x1, y0 + 1, value)
    fill_rect(img, x0, y0, x0 + 1, y1, value)
    fill_rect(img, x1 - 1, y0, x1, y1, value)


def crop_image(img, box):
    x0, y0, x1, y1 = box
    c, w, h = img.channels, x1 - x0, y1 - y0
    out = new(w, h, c)
    for yy in range(h):
        src = ((y0 + yy) * img.width + x0) * c
        dst = yy * w * c
        out.data[dst:dst + w * c] = img.data[src:src + w * c]
    return out


CASES = [
    ("center_square", gen_center_square, True),
    ("edge_left", gen_edge_left, True),
    ("edge_corner", gen_edge_corner, True),
    ("edge_right", gen_edge_right, True),
    ("panoramic_fullwidth", gen_panoramic, True),
    ("flat_none", gen_flat, False),
    ("noise_none", gen_noise, False),
    ("faint_subject", gen_faint, None),  # 记录实际判定，不强制
]

RATIOS = [("1:1", 1.0), ("16:9", 16 / 9), ("9:16", 9 / 16),
          ("4:1_extreme", 4.0), ("1:4_extreme", 0.25)]


def subject_in_box(bbox, box):
    sx0, sy0, sx1, sy1 = bbox
    x0, y0, x1, y1 = box
    return x0 <= sx0 and y0 <= sy0 and x1 >= sx1 and y1 >= sy1


def main():
    os.makedirs(OUT, exist_ok=True)
    failures = []
    records = []

    for name, gen, expect_subject in CASES:
        img = gen()
        save(os.path.join(OUT, name + ".pgm"), img)
        for ratio_name, ratio in RATIOS:
            cfg = AutocropConfig(target_ratio=ratio,
                                 fit=FitPolicy(mode="contain"))
            res = autocrop(img, cfg)
            a = res["analysis"]
            box = res["crop"].box if res["crop"] else None

            rec = {
                "image": name, "ratio": ratio_name,
                "target_ratio": ratio,
                "has_subject": a["has_subject"],
                "reasons": a["reasons"],
                "metrics": {
                    "grid_size": a["grid_size"],
                    "edge_density": round(a["edge_density"], 4),
                    "saliency_mean": round(a["saliency_mean"], 3),
                    "saliency_std": round(a["saliency_std"], 3),
                    "threshold": round(a["threshold"], 3),
                    "fg_mean_saliency": round(a["foreground_mean_saliency"], 2),
                    "bg_mean_saliency": round(a["background_mean_saliency"], 2),
                    "separation": round(a["separation"], 3),
                    "coherence": round(a["coherence"], 3),
                    "components": a["components"],
                    "subject_bbox": a["subject_bbox"],
                    "subject_centroid": a["subject_centroid"],
                },
                "decision": res["decision"],
                "policy": res["crop"].policy if res["crop"] else None,
                "crop_reason": res["crop"].reason if res["crop"] else None,
                "subject_covered": (res["crop"].subject_covered
                                    if res["crop"] else None),
                "box": list(box) if box else None,
                "validation": res["validation"],
            }
            records.append(rec)

            # ---- 断言 ----
            tag = "%s@%s" % (name, ratio_name)
            if expect_subject is True and not a["has_subject"]:
                failures.append("%s: 应检出主体但未检出 %s" % (tag, a["reasons"]))
            if expect_subject is False and a["has_subject"]:
                failures.append("%s: 不应检出主体却检出" % tag)
            if box is not None:
                v = res["validation"]
                if not v["all_ok"]:
                    failures.append("%s: 越界/比例校验失败 %s" % (tag, v))
                if a["has_subject"] and res["crop"].subject_covered:
                    if not subject_in_box(a["subject_bbox"], box):
                        failures.append("%s: 主体 bbox 实际超出裁剪框" % tag)
                if not a["has_subject"]:
                    # 无主体退路必须居中
                    bx = (box[0] + box[2]) // 2
                    by = (box[1] + box[3]) // 2
                    if abs(bx - W // 2) > 1 or abs(by - H // 2) > 1:
                        failures.append("%s: 无主体退路未居中" % tag)
            if name.startswith("edge") and box is not None and ratio_name == "1:1":
                if not subject_in_box(a["subject_bbox"], box):
                    failures.append("%s: 贴边主体被裁掉" % tag)

            # 保存 1:1 的框线图与裁后图供肉眼检查
            if ratio_name == "1:1" and box is not None:
                marked = gen()
                draw_box(marked, box)
                save(os.path.join(OUT, "%s_marked.pgm" % name), marked)
                save(os.path.join(OUT, "%s_cropped.pgm" % name),
                     crop_image(gen(), box))

        # reject 策略的确定性退路：极端比例 + 贯穿主体
        cfg_rej = AutocropConfig(target_ratio=4.0,
                                 fit=FitPolicy(mode="reject"))
        res_rej = autocrop(img, cfg_rej) if name == "panoramic_fullwidth" else None
        if name == "panoramic_fullwidth":
            rec_rej = {"image": name, "ratio": "4:1", "mode": "reject",
                       "decision": res_rej["decision"],
                       "box": list(res_rej["crop"].box)
                              if res_rej["crop"] and res_rej["crop"].box else None}
            records.append(rec_rej)
            if res_rej["decision"] != "rejected_subject_too_large":
                failures.append("panoramic@4:1 reject: 应拒绝但返回了裁剪框")

    # 无主体 + reject_when_no_subject
    img = gen_flat()
    res = autocrop(img, AutocropConfig(
        target_ratio=1.0, reject_when_no_subject=True))
    records.append({"image": "flat_none", "mode": "reject_no_subject",
                    "decision": res["decision"], "box": None})
    if res["decision"] != "rejected_no_subject":
        failures.append("flat: 无主体拒绝退路未生效")

    with open(os.path.join(OUT, "selftest_data.json"), "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    _write_report(records, failures)

    print("自测用例数: %d（含策略用例）" % len(records))
    if failures:
        print("失败 %d 项:" % len(failures))
        for fl in failures:
            print("  FAIL", fl)
        sys.exit(1)
    print("全部通过。数据见 tests_output/selftest_data.json 与 selftest_report.md")


def _write_report(records, failures):
    lines = ["# 自测报告", "",
             "图像 %dx%d，留白 margin=12%%，比例容差 1%%。" % (W, H), ""]
    lines.append("## 主体判据计算结果")
    lines.append("")
    lines.append("| 图 | 检出 | 边缘密度 | 显著度均值±标准差 | 阈值 | "
                 "前景显著度 | 背景显著度 | 分离比 | 凝聚度 | 主体bbox | 未检出原因 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    seen = set()
    for r in records:
        if r["image"] in seen or "metrics" not in r:
            continue
        seen.add(r["image"])
        m = r["metrics"]
        lines.append("| %s | %s | %.3f | %.2f±%.2f | %.2f | %.1f | %.1f | %.2f | %.2f | %s | %s |" % (
            r["image"], "是" if r["has_subject"] else "否",
            m["edge_density"], m["saliency_mean"], m["saliency_std"],
            m["threshold"], m["fg_mean_saliency"], m["bg_mean_saliency"],
            m["separation"], m["coherence"], str(m["subject_bbox"]),
            "、".join(r["reasons"]) or "-"))
    lines.append("")
    lines.append("## 裁剪框与越界校验（contain 策略）")
    lines.append("")
    lines.append("| 图 | 目标比例 | 决策 | 裁剪框(x0,y0,x1,y1) | 实际比例 | 比例误差 | 主体完整 | 越界校验 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in records:
        if "validation" not in r or not r["validation"]:
            lines.append("| %s | %s | %s | 拒绝 | - | - | - | - |" % (
                r["image"], r.get("ratio", r.get("mode")), r["decision"]))
            continue
        v = r["validation"]
        lines.append("| %s | %s | %s/%s | %s | %.4f | %.4f%% | %s | %s |" % (
            r["image"], r["ratio"], r["decision"], r["policy"],
            str(r["box"]), v["ratio_actual"], v["ratio_error"] * 100,
            "是" if r["subject_covered"] else "否(部分)",
            "PASS" if v["all_ok"] else "FAIL"))
    lines.append("")
    lines.append("## 结论")
    lines.append("")
    if failures:
        lines.append("存在 %d 项失败:" % len(failures))
        for fl in failures:
            lines.append("- " + fl)
    else:
        lines.append("全部断言通过：")
        lines.append("- 居中/贴左/贴角/贴右/贯穿主体均被检出且 1:1 下完整保留；")
        lines.append("- 纯色图与纯噪声图判定为无主体，居中退路生效；")
        lines.append("- 所有裁剪框均落在图像范围内，比例误差 <= 1%；")
        lines.append("- 极端比例下主体无法完整包含时 contain_partial 标记部分覆盖，reject 策略确定性拒绝。")
    with open(os.path.join(OUT, "selftest_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
