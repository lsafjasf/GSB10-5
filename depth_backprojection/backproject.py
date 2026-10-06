"""Depth-image back-projection library (Python 3, standard library only).

Camera model
------------
Pinhole intrinsics:
    fx, fy      focal lengths in pixels
    cx, cy      principal point in pixels
    Pixel indices identify pixel centers (center of pixel u is at u + 0).

Distortion: Brown-Conrady (OpenCV-compatible 5-parameter) model applied on the
*normalized* image plane. Let (x, y) be undistorted normalized coordinates:

    r2 = x*x + y*y
    radial  = 1 + k1*r2 + k2*r2^2 + k3*r2^3
    xd = x*radial + 2*p1*x*y + p2*(r2 + 2*x^2)
    yd = y*radial + p1*(r2 + 2*y^2) + 2*p2*x*y

Pixel projection:
    u = fx*xd + cx
    v = fy*yd + cy

Back-projection of one pixel with depth z:
    xd = (u - cx) / fx
    yd = (v - cy) / fy
    (x, y) = Newton inversion of the distortion map at (xd, yd)
    P = (x*z, y*z, z)

Distortion inversion (Newton-Raphson, 5 iterations by default):

    f(x,y) = distort(x,y) - (xd,yd),  solve f = 0 with the analytic Jacobian
    J11 = radial + 2*x^2*d_radial + 2*p1*y + 6*p2*x
    J12 = 2*x*y*d_radial + 2*p1*x + 2*p2*y
    J22 = radial + 2*y^2*d_radial + 6*p1*y + 2*p2*x
    d_radial = k1 + 2*k2*r2 + 3*k3*r2^2
    (x,y) -= J^{-1} f

Newton converges quadratically: 5 iterations reach machine precision even at
image corners with k1 = -0.35. (A plain fixed-point iteration leaves a
residual ~1e-3 normalized there, which is why Newton is used.) Zero
distortion takes a direct (x,y) = (xd,yd) fast path.

Pixel rounding convention
-------------------------
Sub-pixel projection values are rounded half-up to integer pixel indices:
    u_i = floor(u + 0.5)
Worst-case quantization is sqrt(2)/2 pixel, i.e. a spatial error bound of
    e <= z * sqrt(2)/2 / f
at focal length f and depth z.

Missing depth handling
----------------------
A pixel is treated as missing when z <= 0 or z is NaN (NaN makes z > 0 False).
Missing pixels are *marked* deterministically with (NaN, NaN, NaN) in the
dense output array and counted in the returned statistics; they are never
silently back-projected. The dense array is row-major array('d'), so the NaN
triplet can also be filtered downstream as a skip mask.
"""

from array import array
import math

NAN = float("nan")
_DEFAULT_ITERS = 5


class CameraIntrinsics:
    __slots__ = ("fx", "fy", "cx", "cy", "width", "height")

    def __init__(self, fx, fy, cx, cy, width=None, height=None):
        self.fx = float(fx)
        self.fy = float(fy)
        self.cx = float(cx)
        self.cy = float(cy)
        self.width = width
        self.height = height


class Distortion:
    """Brown-Conrady coefficients (k1, k2, p1, p2, k3)."""

    __slots__ = ("k1", "k2", "p1", "p2", "k3")

    def __init__(self, k1=0.0, k2=0.0, p1=0.0, p2=0.0, k3=0.0):
        self.k1 = float(k1)
        self.k2 = float(k2)
        self.p1 = float(p1)
        self.p2 = float(p2)
        self.k3 = float(k3)

    @staticmethod
    def zero():
        return Distortion(0.0, 0.0, 0.0, 0.0, 0.0)

    def any(self):
        return bool(self.k1 or self.k2 or self.p1 or self.p2 or self.k3)


def _radial(r2, k1, k2, k3):
    return 1.0 + r2 * (k1 + r2 * (k2 + r2 * k3))


def normalize_pixel(cam, u, v):
    """Pixel -> distorted normalized coordinates (xd, yd)."""
    return (u - cam.cx) / cam.fx, (v - cam.cy) / cam.fy


def undistort_normalized(xd, yd, dist, iters=_DEFAULT_ITERS):
    """Distorted normalized coords -> undistorted normalized coords (Newton)."""
    k1, k2, p1, p2, k3 = dist.k1, dist.k2, dist.p1, dist.p2, dist.k3
    x, y = xd, yd
    for _ in range(iters):
        x2 = x * x
        y2 = y * y
        r2 = x2 + y2
        radial = _radial(r2, k1, k2, k3)
        d_radial = k1 + 2.0 * k2 * r2 + 3.0 * k3 * r2 * r2
        f1 = x * radial + 2.0 * p1 * x * y + p2 * (r2 + 2.0 * x2) - xd
        f2 = y * radial + p1 * (r2 + 2.0 * y2) + 2.0 * p2 * x * y - yd
        j11 = radial + 2.0 * x2 * d_radial + 2.0 * p1 * y + 6.0 * p2 * x
        j12 = 2.0 * x * y * d_radial + 2.0 * p1 * x + 2.0 * p2 * y
        j22 = radial + 2.0 * y2 * d_radial + 6.0 * p1 * y + 2.0 * p2 * x
        det = j11 * j22 - j12 * j12
        x -= (j22 * f1 - j12 * f2) / det
        y -= (-j12 * f1 + j11 * f2) / det
    return x, y


def backproject_pixel(cam, dist, u, v, z, iters=_DEFAULT_ITERS):
    """Pixel (u, v) and depth z -> 3D point (X, Y, Z)."""
    xd, yd = normalize_pixel(cam, u, v)
    x, y = undistort_normalized(xd, yd, dist, iters)
    return x * z, y * z, z


def project_point(cam, dist, x, y, z):
    """3D point -> distorted sub-pixel image coordinates (u, v)."""
    xn = x / z
    yn = y / z
    r2 = xn * xn + yn * yn
    radial = _radial(r2, dist.k1, dist.k2, dist.k3)
    xd = xn * radial + 2.0 * dist.p1 * xn * yn + dist.p2 * (r2 + 2.0 * xn * xn)
    yd = yn * radial + dist.p1 * (r2 + 2.0 * yn * yn) + 2.0 * dist.p2 * xn * yn
    return cam.fx * xd + cam.cx, cam.fy * yd + cam.cy


def round_half_up(value):
    """Deterministic half-up rounding to the nearest integer pixel."""
    return int(math.floor(value + 0.5))


def is_missing_depth(z):
    # NaN makes (z > 0.0) False, so 0, negatives and NaN are all missing.
    return not (z > 0.0)


def backproject_depthmap(cam, dist, width, height, depths, iters=_DEFAULT_ITERS):
    """Back-project an entire depth map.

    depths: sequence of length width*height in row-major order; may be a
            plain list or an array.array ("f"/"d").
    Returns (points, stats):
        points: array('d') of length 3*width*height, row-major (X,Y,Z);
                missing pixels are marked (NaN, NaN, NaN).
        stats:  dict with 'valid', 'missing', 'total' pixel counts.
    """
    if len(depths) != width * height:
        raise ValueError("depths length %d != width*height %d"
                         % (len(depths), width * height))

    fx, fy, cx, cy = cam.fx, cam.fy, cam.cx, cam.cy
    k1, k2, p1, p2, k3 = dist.k1, dist.k2, dist.p1, dist.p2, dist.k3
    has_dist = dist.any()

    n = width * height
    points = array("d", [0.0]) * (3 * n)

    valid = 0
    missing = 0
    idx = 0
    di = 0
    for v in range(height):
        yd0 = (v - cy) / fy
        for u in range(width):
            z = depths[di]
            di += 1
            if not (z > 0.0):
                points[idx] = NAN
                points[idx + 1] = NAN
                points[idx + 2] = NAN
                idx += 3
                missing += 1
                continue

            xd = (u - cx) / fx
            yd = yd0
            if has_dist:
                x, y = xd, yd
                for _ in range(iters):
                    x2 = x * x
                    y2 = y * y
                    r2 = x2 + y2
                    radial = 1.0 + r2 * (k1 + r2 * (k2 + r2 * k3))
                    d_radial = k1 + 2.0 * k2 * r2 + 3.0 * k3 * r2 * r2
                    f1 = (x * radial + 2.0 * p1 * x * y
                          + p2 * (r2 + 2.0 * x2) - xd)
                    f2 = (y * radial + p1 * (r2 + 2.0 * y2)
                          + 2.0 * p2 * x * y - yd)
                    j11 = radial + 2.0 * x2 * d_radial + 2.0 * p1 * y + 6.0 * p2 * x
                    j12 = 2.0 * x * y * d_radial + 2.0 * p1 * x + 2.0 * p2 * y
                    j22 = radial + 2.0 * y2 * d_radial + 6.0 * p1 * y + 2.0 * p2 * x
                    det = j11 * j22 - j12 * j12
                    x -= (j22 * f1 - j12 * f2) / det
                    y -= (-j12 * f1 + j11 * f2) / det
            else:
                x, y = xd, yd

            points[idx] = x * z
            points[idx + 1] = y * z
            points[idx + 2] = z
            idx += 3
            valid += 1

    return points, {"valid": valid, "missing": missing, "total": n}
