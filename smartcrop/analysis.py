"""Subject detection on a coarse analysis grid.

Criterion (all values in [0, 1] unless noted):
  edge density : Sobel gradient magnitude / 255, per grid cell
  contrast     : local stddev of gray in a 7x7 window / 0.25, via integral images
  saliency     : |gray - global mean| / 0.5
  cell score   = 0.5 * edge + 0.3 * contrast + 0.2 * saliency
  threshold    = clamp(1.6 * mean(score), 0.06, 0.50); cells >= threshold are "hot"
  hot mask is dilated once (3x3), then 4-connected components are extracted.

Confidence that the largest component is a real subject:
  confidence = 0.35 * compactness + 0.30 * dominance + 0.20 * edge_density
             + 0.15 * min(1, area_fraction / 0.02), clamped to [0, 1]
  compactness  = comp_cells / comp_bbox_cells
  dominance    = comp_cells / total_dilated_cells
  edge_density = mean edge value inside component
A subject is accepted when confidence >= 0.25 and 0.002 <= area_fraction <= 0.55.
area_fraction > 0.55 means "hot" texture covers the whole frame (e.g. noise),
which is not a localizable subject, so it is rejected deterministically.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import accumulate

WEIGHT_EDGE = 0.5
WEIGHT_CONTRAST = 0.3
WEIGHT_SALIENCY = 0.2
THRESH_K = 1.6
THRESH_MIN = 0.06
THRESH_MAX = 0.50
CONFIDENCE_MIN = 0.25
AREA_FRACTION_MIN = 0.002
AREA_FRACTION_MAX = 0.55


@dataclass
class SubjectMetrics:
    grid_w: int
    grid_h: int
    score_mean: float
    score_max: float
    threshold: float
    hot_cells: int
    component_cells: int
    component_bbox_cells: int
    edge_density: float
    contrast_mean: float
    saliency_mean: float
    compactness: float
    dominance: float
    area_fraction: float
    confidence: float


@dataclass
class SubjectResult:
    found: bool
    bbox: tuple          # pixel-space (x0, y0, w, h) of subject, or full image
    centroid: tuple      # pixel-space (cx, cy), score-weighted
    confidence: float
    metrics: SubjectMetrics
    touches_edge: bool
    reason: str = ""


def _build_grid(gray, width, height, max_grid):
    step = 1
    while (width + step) // (step + 1) > max_grid or (height + step) // (step + 1) > max_grid:
        step += 1
    gw = (width + step - 1) // step
    gh = (height + step - 1) // step
    grid = []
    for gy in range(gh):
        y = min(gy * step, height - 1)
        base = y * width
        row = [gray[base + min(gx * step, width - 1)] / 255.0 for gx in range(gw)]
        grid.append(row)
    return grid, gw, gh, step


def _integral(grid, gw, gh):
    integ = [[0.0] * (gw + 1)]
    for y in range(gh):
        rowsum = list(accumulate(grid[y]))
        prev = integ[y]
        integ.append([0.0] + [a + b for a, b in zip(prev[1:], rowsum)])
    return integ


def _row_runs(bits, gw):
    """Contiguous runs of set bits in a row mask -> list of (x0, x1] tuples."""
    runs = []
    x = 0
    while x < gw:
        if bits & (1 << x):
            x0 = x
            while x < gw and bits & (1 << x):
                x += 1
            runs.append((x0, x))
        else:
            x += 1
    return runs


def _components_from_bitmask(rows, gw, gh):
    """4-connected components over row bitmasks via run merging."""
    comp_of_run = []          # per row: list of comp ids aligned with runs
    runs_per_row = []
    parent = []

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for y in range(gh):
        runs = _row_runs(rows[y], gw)
        runs_per_row.append(runs)
        ids = []
        prev_runs = runs_per_row[y - 1] if y > 0 else []
        prev_ids = comp_of_run[y - 1] if y > 0 else []
        for (x0, x1) in runs:
            cid = len(parent)
            parent.append(cid)
            # merge with overlapping runs of previous row (4-connectivity)
            for (px0, px1), pid in zip(prev_runs, prev_ids):
                if px0 < x1 and x0 < px1:
                    ra, rb = find(cid), find(pid)
                    if ra != rb:
                        parent[rb] = ra
            ids.append(cid)
        comp_of_run.append(ids)

    comps = {}
    for y in range(gh):
        for (x0, x1), cid in zip(runs_per_row[y], comp_of_run[y]):
            root = find(cid)
            comps.setdefault(root, []).append((y, x0, x1))
    return sorted(comps.values(), key=lambda c: -sum(x1 - x0 for _, x0, x1 in c))


def analyze(image, max_grid=64):
    """Detect the dominant subject. Returns SubjectResult."""
    width, height = image.width, image.height
    grid, gw, gh, step = _build_grid(image.gray, width, height, max_grid)

    total = sum(map(sum, grid))
    gmean = total / (gw * gh)

    # Sobel edge magnitude (borders stay 0)
    edge = [[0.0] * gw for _ in range(gh)]
    for y in range(1, gh - 1):
        up, mid, dn = grid[y - 1], grid[y], grid[y + 1]
        erow = edge[y]
        x = 1
        for a, b, c, d, e, f in zip(up[:-2], mid[:-2], dn[:-2],
                                    up[2:], mid[2:], dn[2:]):
            gx = -a - 2.0 * b - c + d + 2.0 * e + f
            erow[x] = gx  # temporarily store gx
            x += 1
        x = 1
        for a, b, c, d, e, f in zip(up[:-2], up[1:-1], up[2:],
                                    dn[:-2], dn[1:-1], dn[2:]):
            gy = -a - 2.0 * b - c + d + 2.0 * e + f
            gx = erow[x]
            v = (gx * gx + gy * gy) ** 0.5
            erow[x] = v if v < 1.0 else 1.0
            x += 1

    # local contrast via integral images (window radius 3)
    integ = _integral(grid, gw, gh)
    integ2 = _integral([[v * v for v in row] for row in grid], gw, gh)
    contrast = [[0.0] * gw for _ in range(gh)]
    for y in range(gh):
        y0 = y - 3
        if y0 < 0:
            y0 = 0
        y1 = y + 4
        if y1 > gh:
            y1 = gh
        i_y1 = integ[y1]
        i_y0 = integ[y0]
        i2_y1 = integ2[y1]
        i2_y0 = integ2[y0]
        crow = contrast[y]
        hh = y1 - y0
        for x in range(gw):
            x0 = x - 3
            if x0 < 0:
                x0 = 0
            x1 = x + 4
            if x1 > gw:
                x1 = gw
            n = (x1 - x0) * hh
            s = i_y1[x1] - i_y0[x1] - i_y1[x0] + i_y0[x0]
            s2 = i2_y1[x1] - i2_y0[x1] - i2_y1[x0] + i2_y0[x0]
            var = s2 / n - (s / n) * (s / n)
            crow[x] = (var ** 0.5 / 0.25) if var > 0.0 else 0.0
            if crow[x] > 1.0:
                crow[x] = 1.0

    # fused score
    score = []
    smean = 0.0
    smax = 0.0
    inv = 1.0 / 0.5
    for y in range(gh):
        erow, crow, grow = edge[y], contrast[y], grid[y]
        srow = [WEIGHT_EDGE * e + WEIGHT_CONTRAST * c
                + WEIGHT_SALIENCY * (abs(g - gmean) * inv if abs(g - gmean) < 0.5 else 1.0)
                for e, c, g in zip(erow, crow, grow)]
        smean += sum(srow)
        m = max(srow)
        if m > smax:
            smax = m
        score.append(srow)
    smean /= (gw * gh)
    threshold = THRESH_K * smean
    if threshold < THRESH_MIN:
        threshold = THRESH_MIN
    elif threshold > THRESH_MAX:
        threshold = THRESH_MAX

    # hot mask as row bitmasks
    mask_rows = []
    hot_cells = 0
    for y in range(gh):
        bits = 0
        srow = score[y]
        for x in range(gw):
            if srow[x] >= threshold:
                bits |= 1 << x
        hot_cells += bin(bits).count("1")
        mask_rows.append(bits)

    # dilate once (3x3) to fuse fragmented edges of one object
    dil_rows = []
    full = (1 << gw) - 1
    for y in range(gh):
        d = 0
        for dy in (-1, 0, 1):
            ny = y + dy
            if 0 <= ny < gh:
                m = mask_rows[ny]
                d |= m | (m << 1) | (m >> 1)
        dil_rows.append(d & full)

    comps = _components_from_bitmask(dil_rows, gw, gh)
    comps = [c for c in comps if sum(x1 - x0 for _, x0, x1 in c) >= 3]

    def empty_result(reason):
        metrics = SubjectMetrics(
            grid_w=gw, grid_h=gh, score_mean=smean, score_max=smax,
            threshold=threshold, hot_cells=hot_cells, component_cells=0,
            component_bbox_cells=0, edge_density=0.0, contrast_mean=0.0,
            saliency_mean=0.0, compactness=0.0, dominance=0.0,
            area_fraction=0.0, confidence=0.0)
        return SubjectResult(False, (0, 0, width, height),
                             (width / 2.0, height / 2.0), 0.0, metrics,
                             False, reason)

    if not comps:
        return empty_result("no-hot-region")

    comp = comps[0]
    comp_cells = sum(x1 - x0 for _, x0, x1 in comp)
    bx0 = min(x0 for _, x0, _ in comp)
    bx1 = max(x1 for _, _, x1 in comp) - 1
    by0 = comp[0][0]
    by1 = comp[-1][0]
    bbox_cells = (bx1 - bx0 + 1) * (by1 - by0 + 1)
    area_fraction = comp_cells / (gw * gh)
    compactness = comp_cells / bbox_cells
    total_dilated = sum(sum(x1 - x0 for _, x0, x1 in c) for c in comps)
    dominance = comp_cells / total_dilated if total_dilated else 0.0

    edge_sum = 0.0
    contrast_sum = 0.0
    sal_sum = 0.0
    wsum = 0.0
    cx = 0.0
    cy = 0.0
    for y, x0, x1 in comp:
        erow = edge[y]
        crow = contrast[y]
        grow = grid[y]
        srow = score[y]
        for x in range(x0, x1):
            edge_sum += erow[x]
            contrast_sum += crow[x]
            g = grow[x] - gmean
            sal_sum += (abs(g) * inv) if abs(g) < 0.5 else 1.0
            s = srow[x] + 1e-6
            wsum += s
            cx += x * s
            cy += y * s
    edge_density = edge_sum / comp_cells
    contrast_mean = contrast_sum / comp_cells
    saliency_mean = sal_sum / comp_cells
    confidence = (0.35 * compactness + 0.30 * dominance + 0.20 * edge_density
                  + 0.15 * min(1.0, area_fraction / 0.02))
    if confidence > 1.0:
        confidence = 1.0

    metrics = SubjectMetrics(
        grid_w=gw, grid_h=gh, score_mean=smean, score_max=smax,
        threshold=threshold, hot_cells=hot_cells, component_cells=comp_cells,
        component_bbox_cells=bbox_cells, edge_density=edge_density,
        contrast_mean=contrast_mean, saliency_mean=saliency_mean,
        compactness=compactness, dominance=dominance,
        area_fraction=area_fraction, confidence=confidence)

    reason = "ok"
    if area_fraction > AREA_FRACTION_MAX:
        reason = "hot-region-fills-frame"
    elif confidence < CONFIDENCE_MIN:
        reason = "low-confidence"
    elif area_fraction < AREA_FRACTION_MIN:
        reason = "subject-too-small"
    if reason != "ok":
        return SubjectResult(False, (0, 0, width, height),
                             (width / 2.0, height / 2.0),
                             confidence, metrics, False, reason)

    gcx = cx / wsum
    gcy = cy / wsum

    # map grid bbox to pixels, add 8% margin, clamp to image
    margin = 0.08
    px0 = bx0 * step
    py0 = by0 * step
    px1 = min(width, (bx1 + 1) * step)
    py1 = min(height, (by1 + 1) * step)
    mx = (px1 - px0) * margin
    my = (py1 - py0) * margin
    px0 = max(0, int(px0 - mx))
    py0 = max(0, int(py0 - my))
    px1 = min(width, int(px1 + mx + 0.5))
    py1 = min(height, int(py1 + my + 0.5))

    pcx = min(width - 1.0, max(0.0, gcx * step + step / 2.0))
    pcy = min(height - 1.0, max(0.0, gcy * step + step / 2.0))

    touches_edge = (px0 == 0 or py0 == 0 or px1 >= width or py1 >= height)

    return SubjectResult(True, (px0, py0, px1 - px0, py1 - py0),
                         (pcx, pcy), confidence, metrics, touches_edge, "ok")
