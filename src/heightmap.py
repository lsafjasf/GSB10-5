"""Heightmap interpolation and normal computation.

Conventions
-----------
* Regular grid, x = column index * cell_size, y = row index * cell_size, z up.
* A height sample may be missing; it is stored as NaN.
* Surface normal of z = h(x, y) is

      r(x, y) = (x, y, h(x, y))
      n = dr/dx x dr/dy = (-dh/dx, -dh/dy, 1) / |...|

  so the normal always points to the upper side (n_z > 0).
"""

from array import array
from collections import deque
import math

NAN = float("nan")


class HeightMap:
    def __init__(self, width, height, cell_size, heights):
        self.width = width
        self.height = height
        self.cell_size = float(cell_size)
        if len(heights) != width * height:
            raise ValueError("heights length does not match width*height")
        self.z = array("d", heights)


def make_map(width, height, cell_size, fn):
    """Build a HeightMap from an analytic function fn(col, row) -> z or None."""
    z = array("d", [0.0]) * (width * height)
    for r in range(height):
        base = r * width
        for c in range(width):
            v = fn(c, r)
            z[base + c] = NAN if v is None else v
    return HeightMap(width, height, cell_size, z)


def count_missing(hm):
    return sum(1 for v in hm.z if math.isnan(v))


def fill_missing(hm):
    """Fill missing samples deterministically.

    Rule: every missing cell receives the height of the nearest valid cell
    (4-neighbourhood graph distance). Ties are broken by the smaller linear
    scan index (row*width+col) of the valid source cell, then of the cell
    through which it was reached. Multi-source BFS with sources enqueued in
    scan order makes this fully deterministic.

    Returns the number of filled cells. Raises ValueError if the grid has no
    valid cell at all (no information -> rejected, no fabrication).
    """
    w, h, z = hm.width, hm.height, hm.z
    n = w * h
    filled = bytearray(n)
    dq = deque()
    for i in range(n):
        if not math.isnan(z[i]):
            dq.append((i, z[i]))
            filled[i] = 1
    if not dq:
        raise ValueError("height grid contains no valid samples; rejected")
    result = array("d", z)
    while dq:
        i, val = dq.popleft()
        r, c = divmod(i, w)
        if c > 0:
            j = i - 1
            if not filled[j]:
                filled[j] = 1
                result[j] = val
                dq.append((j, val))
        if c + 1 < w:
            j = i + 1
            if not filled[j]:
                filled[j] = 1
                result[j] = val
                dq.append((j, val))
        if r > 0:
            j = i - w
            if not filled[j]:
                filled[j] = 1
                result[j] = val
                dq.append((j, val))
        if r + 1 < h:
            j = i + w
            if not filled[j]:
                filled[j] = 1
                result[j] = val
                dq.append((j, val))
    n_filled = sum(1 for v in z if math.isnan(v))
    hm.z = result
    return n_filled


def _bilinear(z, w, h, gx, gy):
    """Sample at grid coordinates gx,gy (clamped to bounds). NaN propagates."""
    if gx <= 0.0:
        x0 = 0
        tx = 0.0
    elif gx >= w - 1:
        x0 = w - 2
        tx = 1.0
    else:
        x0 = int(gx)
        tx = gx - x0
    if gy <= 0.0:
        y0 = 0
        ty = 0.0
    elif gy >= h - 1:
        y0 = h - 2
        ty = 1.0
    else:
        y0 = int(gy)
        ty = gy - y0
    i00 = y0 * w + x0
    v00 = z[i00]
    v10 = z[i00 + 1]
    v01 = z[i00 + w]
    v11 = z[i00 + w + 1]
    a = v00 + (v10 - v00) * tx
    b = v01 + (v11 - v01) * tx
    return a + (b - a) * ty


def resample(src, out_w, out_h):
    """Upsample (or downsample) by bilinear interpolation.

    Corners map to corners: src_x = cx*(src_w-1)/(out_w-1).
    Any NaN among the four surrounding samples propagates as NaN.
    """
    out = array("d", [0.0]) * (out_w * out_h)
    z, w, h = src.z, src.width, src.height
    sx = (w - 1) / (out_w - 1)
    sy = (h - 1) / (out_h - 1)
    k = 0
    gy = 0.0
    for r in range(out_h):
        gx = 0.0
        for c in range(out_w):
            out[k] = _bilinear(z, w, h, gx, gy)
            gx += sx
            k += 1
        gy += sy
    out_cell = src.cell_size * (w - 1) / (out_w - 1)
    return HeightMap(out_w, out_h, out_cell, out)


def compute_normals(hm):
    """Per-vertex unit normals from finite differences of heights.

    Interior vertices use the central difference
        dh/dx ~ (z[c+1]-z[c-1]) / (2*cell_size)
    and boundary vertices use one-sided differences.
    Returns three array('f') buffers (nx, ny, nz). NaN heights raise
    ValueError: fill missing data before computing normals.
    """
    w, h, s, z = hm.width, hm.height, hm.cell_size, hm.z
    n = w * h
    nx = array("d", [0.0]) * n
    ny = array("d", [0.0]) * n
    nz = array("d", [0.0]) * n
    for v in z:
        if math.isnan(v):
            raise ValueError("NaN height present; fill or reject missing data first")
    inv_s = 1.0 / s
    inv_2s = 0.5 / s
    for r in range(h):
        base = r * w
        for c in range(w):
            i = base + c
            if c == 0:
                dzx = (z[i + 1] - z[i]) * inv_s
            elif c == w - 1:
                dzx = (z[i] - z[i - 1]) * inv_s
            else:
                dzx = (z[i + 1] - z[i - 1]) * inv_2s
            if r == 0:
                dzy = (z[i + w] - z[i]) * inv_s
            elif r == h - 1:
                dzy = (z[i] - z[i - w]) * inv_s
            else:
                dzy = (z[i + w] - z[i - w]) * inv_2s
            inv_len = 1.0 / math.sqrt(dzx * dzx + dzy * dzy + 1.0)
            nx[i] = -dzx * inv_len
            ny[i] = -dzy * inv_len
            nz[i] = inv_len
    return nx, ny, nz


def angle_error_deg(n1, n2, i):
    """Angle in degrees between normals of tuple-buffers n1=(nx,ny,nz)."""
    x1, y1, z1 = n1
    x2, y2, z2 = n2
    dot = x1[i] * x2[i] + y1[i] * y2[i] + z1[i] * z2[i]
    if dot > 1.0:
        dot = 1.0
    elif dot < -1.0:
        dot = -1.0
    return math.degrees(math.acos(dot))


def normal_error_stats(ref, test, mask=None):
    """Mean/RMS/max angle error (degrees) over all points or a boolean mask."""
    rx, ry, rz = ref
    tx, ty, tz = test
    n = len(rx)
    total = 0.0
    sq = 0.0
    mx = 0.0
    cnt = 0
    for i in range(n):
        if mask is not None and not mask[i]:
            continue
        d = angle_error_deg(ref, test, i)
        total += d
        sq += d * d
        if d > mx:
            mx = d
        cnt += 1
    return {
        "count": cnt,
        "mean_deg": total / cnt,
        "rms_deg": math.sqrt(sq / cnt),
        "max_deg": mx,
    }


def validate_normals(n, tol=1e-5):
    """Per-point checks: finite, unit length (within tol), upper side (nz>0)."""
    nx, ny, nz = n
    bad_len = []
    bad_side = []
    bad_finite = []
    for i in range(len(nx)):
        x, y, z = nx[i], ny[i], nz[i]
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
            bad_finite.append(i)
            continue
        length = math.sqrt(x * x + y * y + z * z)
        if abs(length - 1.0) > tol:
            bad_len.append((i, length))
        if z <= 0.0:
            bad_side.append(i)
    return {
        "points": len(nx),
        "bad_finite": bad_finite,
        "bad_length": bad_len,
        "bad_side": bad_side,
        "tol": tol,
    }
