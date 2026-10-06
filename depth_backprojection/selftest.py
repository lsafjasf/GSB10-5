"""Self-test, cross-check and benchmark for backproject.py.

Run:
    python3 selftest.py

Pure standard library. All lengths in metres; pixel indices identify pixel
centers. The cross-check constructs known 3D points in the camera frustum,
projects them with the forward model, and back-projects the resulting pixels:
forward/inverse consistency is exactly the property being verified.
"""

import math
import random
import time
import tracemalloc
from array import array

from backproject import (
    CameraIntrinsics,
    Distortion,
    backproject_depthmap,
    backproject_pixel,
    project_point,
    round_half_up,
    undistort_normalized,
)


W, H = 640, 480
FX = FY = 525.0
CX, CY = 319.5, 239.5
DEPTHS = (0.5, 1.2, 3.0)

ZERO_DIST = Distortion.zero()
STRONG_DIST = Distortion(k1=-0.35, k2=0.15, p1=0.0010, p2=-0.0020, k3=-0.03)


def make_camera():
    return CameraIntrinsics(FX, FY, CX, CY, W, H)


def err_stats(values):
    mean = sum(values) / len(values)
    rmse = math.sqrt(sum(v * v for v in values) / len(values))
    return mean, rmse, max(values)


def fmt(values):
    return "mean=%.3e  rmse=%.3e  max=%.3e" % err_stats(values)


def norm_radius(cam, u, v):
    return math.hypot((u - cam.cx) / cam.fx, (v - cam.cy) / cam.fy)


def known_3d_points(cam, dist):
    """Known 3D points (X, Y, Z) spread across the frustum, corners included.

    The undistorted corner rays are obtained by Newton-inverting the corner
    pixels, so the grid genuinely reaches the FOV edge for this distortion.
    """
    cxu, cyu = undistort_normalized((0.0 - cam.cx) / cam.fx,
                                    (0.0 - cam.cy) / cam.fy, dist)
    xs = [cxu * t / 4.0 for t in range(-4, 5)]
    ys = [cyu * t / 4.0 for t in range(-4, 5)]
    pts = []
    for xn in xs:
        for yn in ys:
            for z in DEPTHS:
                pts.append((xn * z, yn * z, z))
    rng = random.Random(20261006)
    for _ in range(120):
        xn = rng.uniform(-0.98, 0.98) * abs(cxu)
        yn = rng.uniform(-0.98, 0.98) * abs(cyu)
        z = rng.uniform(0.4, 4.0)
        pts.append((xn * z, yn * z, z))

    # sub-pixel corner probes: known points whose projections sit a fraction
    # of a pixel off the four frame corners (real rounding error at FOV edge)
    corner_pixels = ((0.0, 0.0), (W - 1.0, 0.0),
                     (0.0, H - 1.0), (W - 1.0, H - 1.0))
    for cux, cuy in corner_pixels:
        xdd = (cux - cam.cx) / cam.fx
        ydd = (cuy - cam.cy) / cam.fy
        for ox, oy in ((0.00030, 0.00020), (-0.00020, 0.00030),
                       (0.00015, -0.00025), (-0.00025, -0.00015)):
            xn, yn = undistort_normalized(xdd + ox, ydd + oy, dist)
            for z in (0.5, 3.0):
                pts.append((xn * z, yn * z, z))

    rmax = math.hypot(max(abs(0.0 - cam.cx), abs(W - 1.0 - cam.cx)) / cam.fx,
                      max(abs(0.0 - cam.cy), abs(H - 1.0 - cam.cy)) / cam.fy)
    return pts, rmax


def roundtrip_test(cam, dist, label):
    pts, rmax = known_3d_points(cam, dist)
    quant = math.sqrt(2.0) / 2.0

    err_exact = []      # 3D error, sub-pixel back-projection (model inverse)
    err_round = []      # 3D error, integer-pixel back-projection (quantized)
    edge_round, center_round, corner_round = [], [], []
    bounds = []
    in_frame = 0

    for (px, py, pz) in pts:
        uf, vf = project_point(cam, dist, px, py, pz)
        if not (0.0 <= uf <= W - 1 and 0.0 <= vf <= H - 1):
            continue
        in_frame += 1

        # sub-pixel: back-project the exact projection -> isolates model error
        qx, qy, qz = backproject_pixel(cam, dist, uf, vf, pz)
        err_exact.append(math.dist((px, py, pz), (qx, qy, qz)))

        # integer pixel: deterministic half-up rounding, then back-project
        ui, vi = round_half_up(uf), round_half_up(vf)
        rx, ry, rz = backproject_pixel(cam, dist, ui, vi, pz)
        e = math.dist((px, py, pz), (rx, ry, rz))
        err_round.append(e)
        bounds.append(pz * quant / min(cam.fx, cam.fy))

        rn = norm_radius(cam, uf, vf)
        if rn > 0.85 * rmax:
            edge_round.append(e)
        elif rn < 0.3:
            center_round.append(e)
        if (ui in (0, 1, W - 2, W - 1)) and (vi in (0, 1, H - 2, H - 1)):
            corner_round.append(e)

    print("[%s] known-3D-point projection cross-check, %d/%d points in frame"
          % (label, in_frame, len(pts)))
    print("  3D err, sub-pixel back-projection (m): " + fmt(err_exact))
    print("  3D err, integer pixel          (m): " + fmt(err_round))
    print("  quantization bound z*sqrt(2)/2f (m): " + fmt(bounds))
    print("  center region r<.3 (n=%d) (m): %s"
          % (len(center_round), fmt(center_round)))
    print("  edge region r>%.3f (n=%d) (m): %s"
          % (0.85 * rmax, len(edge_round), fmt(edge_round)))

    assert max(err_exact) < 1e-9, max(err_exact)
    assert max(err_round) <= max(bounds) * 1.000001
    assert max(err_round) > 0.0  # rounding error is real, not degenerate
    assert len(edge_round) >= 10 and len(corner_round) >= 4
    print("  4-corner pixel probes (n=%d)    (m): %s"
          % (len(corner_round), fmt(corner_round)))
    print("  assertions: model inverse <1e-9 m; rounded err within "
          "quantization bound; edge+corner coverage  OK")
    print()


def missing_depth_test(cam):
    """Deterministic handling of zero / negative / NaN depths."""
    w, h = 4, 3
    depths = [
        0.0, 1.0, 2.0, float("nan"),
        -1.0, 1.5, 0.0, 2.5,
        0.0, 3.0, float("nan"), 2.0,
    ]
    points, stats = backproject_depthmap(cam, ZERO_DIST, w, h, depths)
    print("[missing] 4x3 map: zeros x3, negative x1, NaN x2")
    print("  stats: %s" % stats)
    assert stats == {"valid": 6, "missing": 6, "total": 12}

    for i, z in enumerate(depths):
        trip = points[3 * i:3 * i + 3]
        if not (z > 0.0):
            assert all(math.isnan(t) for t in trip), trip
        else:
            u, v = i % w, i // w
            ref = backproject_pixel(cam, ZERO_DIST, u, v, z)
            assert all(abs(a - b) < 1e-12 for a, b in zip(trip, ref))
    print("  missing pixels marked (NaN,NaN,NaN); valid pixels match "
          "single-pixel API  OK")
    print()


def distortion_compensation_compare(cam):
    """Same depth map back-projected with and without distortion terms."""
    depths = array("f", [0.0]) * (W * H)
    i = 0
    for v in range(H):
        for u in range(W):
            depths[i] = 2.0 + 0.002 * v + 0.3 * math.sin(u / 90.0)
            i += 1

    pts_full, _ = backproject_depthmap(cam, STRONG_DIST, W, H, depths)
    pts_none, _ = backproject_depthmap(cam, ZERO_DIST, W, H, depths)

    rmax = math.hypot(max(abs(0.0 - cam.cx), abs(W - 1.0 - cam.cx)) / cam.fx,
                      max(abs(0.0 - cam.cy), abs(H - 1.0 - cam.cy)) / cam.fy)
    all_d, edge_d, center_d = [], [], []
    edge_xy = []
    for v in range(H):
        for u in range(W):
            i = 3 * (v * W + u)
            d = math.sqrt((pts_full[i] - pts_none[i]) ** 2
                          + (pts_full[i + 1] - pts_none[i + 1]) ** 2)
            all_d.append(d)
            rn = norm_radius(cam, u, v)
            if rn > 0.85 * rmax:
                edge_d.append(d)
                edge_xy.append(math.hypot(pts_full[i] - pts_none[i],
                                          pts_full[i + 1] - pts_none[i + 1]))
            elif rn < 0.3:
                center_d.append(d)

    print("[distortion on/off] strong distortion k1=-0.35 k2=0.15 "
          "k3=-0.03 p1=1e-3 p2=-2e-3, same depth map both ways")
    print("  |3D displacement| compensated vs naive (m), all pixels : "
          + fmt(all_d))
    print("  center region r<.3 (n=%d) (m): %s"
          % (len(center_d), fmt(center_d)))
    print("  edge region r>%.3f (n=%d) (m): %s"
          % (0.85 * rmax, len(edge_d), fmt(edge_d)))
    print("  lateral (X,Y) displacement at edge (m): " + fmt(edge_xy))
    ratio = err_stats(edge_d)[1] / err_stats(center_d)[1]
    print("  edge/center RMSE ratio: %.1fx" % ratio)
    print()


def benchmark_1mp(cam):
    """1,000,000-pixel depth map: timing and peak memory."""
    w = h = 1000
    n = w * h
    depths = array("f", [0.0]) * n
    for i in range(n):
        depths[i] = 1.0 + (i % 97) * 0.01 + 0.2 * math.sin(i / 50000.0)
        if i % 1000 == 0:
            depths[i] = 0.0  # 0.1% missing

    times = []
    peak_bytes = None
    stats = None
    for run in range(3):
        if run == 0:
            tracemalloc.start()
        t0 = time.perf_counter()
        points, stats = backproject_depthmap(cam, STRONG_DIST, w, h, depths)
        dt = time.perf_counter() - t0
        if run == 0:
            _, peak_bytes = tracemalloc.get_traced_memory()
            tracemalloc.stop()
        times.append(dt)

    rss_kb = _rss_peak_kb()
    print("[benchmark] 1000x1000 depth map (array('f'), 5 Newton iters, "
          "strong distortion)")
    print("  times (3 runs) s : %.3f / %.3f / %.3f  (best %.3f, "
          "%.0f kpix/s best)"
          % (times[0], times[1], times[2], min(times),
             n / min(times) / 1000.0))
    print("  stats: %s (%.2f%% missing)"
          % (stats, 100.0 * stats["missing"] / stats["total"]))
    print("  tracemalloc peak during call: %.1f MiB" % (peak_bytes / 1048576.0))
    print("  process max RSS             : %.1f MiB" % (rss_kb / 1024.0))
    print("  output size                 : %.1f MiB (array('d'), 3*%d)"
          % (3 * n * 8 / 1048576.0, n))
    print()


def _rss_peak_kb():
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except Exception:
        return float("nan")


def main():
    cam = make_camera()
    print("camera: %dx%d fx=fy=%.1f cx=%.1f cy=%.1f" % (W, H, FX, CX, CY))
    print()
    roundtrip_test(cam, ZERO_DIST, "no distortion")
    roundtrip_test(cam, STRONG_DIST, "strong distortion")
    missing_depth_test(cam)
    distortion_compensation_compare(cam)
    benchmark_1mp(cam)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
