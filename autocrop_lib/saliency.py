"""主体判据：边缘密度 + 局部对比度 + 显著性 + 空间凝聚度。

流程（所有中间数值都会在结果字典中返回，可逐项核对）:
1. 灰度化后最近邻缩到不超过 ``max_long_edge`` 的分析网格。
2. 边缘响应 e[y,x] = |水平中心差分| 与 |垂直中心差分| 的最大值（0..255）。
   边缘密度 = 边缘响应 >= EDGE_RESP_FLOOR 的像素占比。
3. 局部对比度 c[y,x] = |灰度 - 积分图局部窗口均值|（0..255）。
4. 显著度 s[y,x] = (0.55*c + 0.45*e) * 中心先验 f，
   f = 1/(1+((x-cx)/(w/2))^2+((y-cy)/(h/2))^2)（软先验，不排斥贴边主体）。
5. 阈值 T = max(MIN_SALIENCY, mean(s)+1.5*std(s))，二值化后做 4 连通域。
6. 主体判定（四条都必须满足，任一不满足即“无明显主体”）:
   a) 边缘密度 >= EDGE_DENSITY_FLOOR
   b) 最大连通域面积 >= MIN_AREA_RATIO
   c) 最大连通域平均显著度 >= COMPONENT_SALIENCY_FLOOR
   d) 凝聚度 = 组件显著度之和 / 全图显著度之和 >= COHERENCE_FLOOR
   另要求前景/背景显著度分离比 >= SEPARATION_FLOOR。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CriterionConfig:
    max_long_edge: int = 64
    window_ratio: float = 0.08          # 局部窗口半径 / 网格短边
    edge_resp_floor: int = 40           # 单像素边缘响应下限
    edge_density_floor: float = 0.005   # 判据 a（仅排除纯平图）
    min_area_ratio: float = 0.0025      # 判据 b（组件面积占比）
    component_saliency_floor: int = 36  # 判据 c（组件平均显著度，0..255）
    coherence_floor: float = 0.18       # 判据 d
    separation_floor: float = 1.8       # 前景/背景显著度分离比
    min_saliency_threshold: int = 18    # 二值化阈值绝对下限
    std_k: float = 1.5


def _downsample_luma(image, max_long_edge):
    W, H = image.width, image.height
    gray = image.luma()
    long_side = max(W, H)
    if long_side <= max_long_edge:
        return gray, W, H
    if W >= H:
        gw = max_long_edge
        gh = max(1, round(H * max_long_edge / W))
    else:
        gh = max_long_edge
        gw = max(1, round(W * max_long_edge / H))
    grid = bytearray(gw * gh)
    for gy in range(gh):
        sy = gy * H // gh
        src_row = sy * W
        out_row = gy * gw
        for gx in range(gw):
            sx = gx * W // gw
            grid[out_row + gx] = gray[src_row + min(sx, W - 1)]
    return grid, gw, gh


def _integral(grid, w, h):
    ii = [0] * ((w + 1) * (h + 1))
    stride = w + 1
    for y in range(h):
        row_acc = 0
        src = y * w
        dst = (y + 1) * stride + 1
        for x in range(w):
            row_acc += grid[src + x]
            ii[dst + x] = ii[dst + x - stride] + row_acc
    return ii


def _local_mean(ii, w, h, x, y, r):
    x0 = x - r if x - r > 0 else 0
    x1 = x + r + 1 if x + r + 1 < w else w
    y0 = y - r if y - r > 0 else 0
    y1 = y + r + 1 if y + r + 1 < h else h
    stride = w + 1
    total = (ii[y1 * stride + x1] - ii[y0 * stride + x1]
             - ii[y1 * stride + x0] + ii[y0 * stride + x0])
    return total / ((x1 - x0) * (y1 - y0))


def _components(mask, w, h):
    """4 连通域，返回 [(id起点索引列表的标签, bbox, area)]（就地填充 label）。"""
    label = bytearray(w * h)
    comps = []
    for start in range(w * h):
        if mask[start] == 0 or label[start] != 0:
            continue
        cid = len(comps) + 1
        if cid > 255:
            break
        stack = [start]
        label[start] = cid
        minx = maxx = start % w
        miny = maxy = start // w
        area = 0
        while stack:
            p = stack.pop()
            area += 1
            x = p % w
            y = p // w
            if x < minx: minx = x
            if x > maxx: maxx = x
            if y < miny: miny = y
            if y > maxy: maxy = y
            if x > 0 and mask[p - 1] and label[p - 1] == 0:
                label[p - 1] = cid; stack.append(p - 1)
            if x + 1 < w and mask[p + 1] and label[p + 1] == 0:
                label[p + 1] = cid; stack.append(p + 1)
            if y > 0 and mask[p - w] and label[p - w] == 0:
                label[p - w] = cid; stack.append(p - w)
            if y + 1 < h and mask[p + w] and label[p + w] == 0:
                label[p + w] = cid; stack.append(p + w)
        comps.append({"bbox": (minx, miny, maxx + 1, maxy + 1),
                      "area": area, "cid": cid})
    return comps, label


def analyze_subject(image, config=None):
    """计算主体判据。返回包含全部中间数值的结果字典。"""
    cfg = config or CriterionConfig()
    grid, gw, gh = _downsample_luma(image, cfg.max_long_edge)
    n = gw * gh

    # --- 1) 边缘响应（中心差分，边界单侧差分）---
    edge = bytearray(n)
    edge_pixels = 0
    for y in range(gh):
        row = y * gw
        for x in range(gw):
            p = row + x
            if x == 0:
                dx = abs(grid[p + 1] - grid[p]) if gw > 1 else 0
            elif x == gw - 1:
                dx = abs(grid[p] - grid[p - 1])
            else:
                dx = abs(grid[p + 1] - grid[p - 1])
            if y == 0:
                dy = abs(grid[p + gw] - grid[p]) if gh > 1 else 0
            elif y == gh - 1:
                dy = abs(grid[p] - grid[p - gw])
            else:
                dy = abs(grid[p + gw] - grid[p - gw])
            e = dx if dx > dy else dy
            if e > 255:
                e = 255
            edge[p] = e
            if e >= cfg.edge_resp_floor:
                edge_pixels += 1
    edge_density = edge_pixels / n

    # --- 2) 局部对比度（积分图窗口均值）---
    ii = _integral(grid, gw, gh)
    r = max(1, int(round(min(gw, gh) * cfg.window_ratio)))
    contrast = bytearray(n)
    for y in range(gh):
        row = y * gw
        for x in range(gw):
            p = row + x
            d = abs(grid[p] - _local_mean(ii, gw, gh, x, y, r))
            contrast[p] = 255 if d > 255 else int(d)

    # --- 3) 显著度 = 对比/边缘融合 × 中心软先验 ---
    sal = [0.0] * n
    cx0 = (gw - 1) / 2.0
    cy0 = (gh - 1) / 2.0
    sx = max(gw / 2.0, 1e-9)
    sy = max(gh / 2.0, 1e-9)
    total_sal = 0.0
    for y in range(gh):
        ry = (y - cy0) / sy
        row = y * gw
        for x in range(gw):
            rx = (x - cx0) / sx
            prior = 1.0 / (1.0 + rx * rx + ry * ry)
            v = (0.55 * contrast[row + x] + 0.45 * edge[row + x]) * prior
            sal[row + x] = v
            total_sal += v

    mean_sal = total_sal / n
    var = sum((v - mean_sal) ** 2 for v in sal) / n
    std_sal = var ** 0.5
    threshold = max(cfg.min_saliency_threshold,
                    mean_sal + cfg.std_k * std_sal)

    # --- 4) 二值化 + 连通域 ---
    mask = bytearray(n)
    for i, v in enumerate(sal):
        if v >= threshold:
            mask[i] = 1
    comps, label = _components(mask, gw, gh)

    # 组件统计（面积、平均显著度、bbox）
    stats = []
    min_area = max(6.0, cfg.min_area_ratio * n)
    for comp in comps:
        x0, y0, x1, y1 = comp["bbox"]
        sal_sum = 0.0
        for yy in range(y0, y1):
            base = yy * gw
            for xx in range(x0, x1):
                if label[base + xx] == comp["cid"]:
                    sal_sum += sal[base + xx]
        stats.append({
            "bbox_grid": comp["bbox"],
            "area_px": comp["area"],
            "area_ratio": comp["area"] / n,
            "mean_saliency": sal_sum / comp["area"],
            "sal_sum": sal_sum,
        })
    stats.sort(key=lambda s: s["area_px"], reverse=True)

    reasons = []
    if edge_density < cfg.edge_density_floor:
        reasons.append("edge_density<%.3f" % cfg.edge_density_floor)
    if not stats or stats[0]["area_px"] < min_area:
        reasons.append("component_area<%.0fpx" % min_area)
    if stats and stats[0]["mean_saliency"] < cfg.component_saliency_floor:
        reasons.append("component_mean_saliency<%d"
                       % cfg.component_saliency_floor)

    # 凝聚度 / 前背景分离
    fg_sum = stats[0]["sal_sum"] if stats else 0.0
    bg_sum = max(total_sal - fg_sum, 1e-9)
    bg_pixels = max(n - (stats[0]["area_px"] if stats else 0), 1)
    coherence = fg_sum / max(total_sal, 1e-9)
    fg_mean = stats[0]["mean_saliency"] if stats else 0.0
    bg_mean = bg_sum / bg_pixels
    separation = fg_mean / max(bg_mean, 1e-9)
    if coherence < cfg.coherence_floor:
        reasons.append("coherence<%.2f" % cfg.coherence_floor)
    if separation < cfg.separation_floor:
        reasons.append("separation<%.2f" % cfg.separation_floor)

    has_subject = not reasons

    # --- 5) 映射回原图坐标（网格单元覆盖的原像素区间）---
    W, H = image.width, image.height
    if stats:
        gx0, gy0, gx1, gy1 = stats[0]["bbox_grid"]
        bx0 = gx0 * W // gw
        by0 = gy0 * H // gh
        bx1 = -(-gx1 * W // gw)   # ceil
        by1 = -(-gy1 * H // gh)
        ccx = int(round((gx0 + gx1) / 2.0 * W / gw))
        ccy = int(round((gy0 + gy1) / 2.0 * H / gh))
    else:
        bx0 = by0 = bx1 = by1 = ccx = ccy = 0

    return {
        "has_subject": has_subject,
        "reasons": reasons,
        "grid_size": [gw, gh],
        "edge_density": edge_density,
        "edge_pixels": edge_pixels,
        "saliency_mean": mean_sal,
        "saliency_std": std_sal,
        "threshold": threshold,
        "foreground_mean_saliency": fg_mean,
        "background_mean_saliency": bg_mean,
        "separation": separation,
        "coherence": coherence,
        "components": [
            {"bbox_grid": s["bbox_grid"], "area_px": s["area_px"],
             "area_ratio": s["area_ratio"],
             "mean_saliency": s["mean_saliency"]}
            for s in stats
        ],
        "subject_bbox": [bx0, by0, bx1, by1],
        "subject_centroid": [ccx, ccy],
        "config": cfg.__dict__,
        "saliency_map": sal,
        "_grid": grid,
    }
