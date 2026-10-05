"""球面坐标插值库（仅标准库）。

方法：大圆插值（great-circle interpolation），全程在球面坐标下进行：
  1. haversine 公式求两点圆心角 sigma（对小距离数值稳定）；
  2. 初始方位角公式求 p1 -> p2 的大圆方位 theta；
  3. 从 p1 沿方位角 theta 前进角距离 t*sigma（destination-point 公式）。

经度在插值前先归一化差值到 [-180, 180)，因此跨经度分界（如 170 -> -170）
永远走短弧，不会绕地球一圈。

特殊情形（均有确定结果）：
  - 两点重合（sigma == 0）：任意 t 返回 p1。
  - 对跖点（sigma == pi）：大圆不唯一，约定走 p1 所在半球的经线
    （lat1 >= 0 经北极，否则经南极），路径确定。
  - 极点附近/正好在极点：方位角公式在极点依然成立（极点经度任意，
    输出经度按大圆几何自然翻转），结果确定。
"""

import math

__all__ = ["slerp", "slerp_path", "central_angle", "normalize_lon"]

_ANTIPODAL_EPS = 1e-7   # 弧度；sigma 与 pi 之差小于该值即按对跖点处理。
# 取 1e-7 是因为圆心角接近 pi 时大圆方向本身病态（方位角误差 ~1e-16/(pi-sigma)），
# 在该带宽内“真实大圆”在双精度下无意义，统一按约定的经线路径处理以保证确定性。
_COINCIDENT_EPS = 1e-15  # 弧度；sigma 小于该值即视为两点重合


def normalize_lon(lon):
    """经度归一化到 (-180, 180]。"""
    lon = math.fmod(lon + 180.0, 360.0)
    if lon < 0.0:
        lon += 360.0
    return lon - 180.0


def central_angle(lat1, lon1, lat2, lon2):
    """两点间圆心角（弧度），haversine 形式。"""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = phi2 - phi1
    dlam = math.radians(normalize_lon(lon2 - lon1))
    h = (math.sin(dphi / 2.0) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0) ** 2)
    h = min(1.0, max(0.0, h))
    return 2.0 * math.atan2(math.sqrt(h), math.sqrt(1.0 - h))


def slerp(lat1, lon1, lat2, lon2, t):
    """球面线性插值，t in [0, 1]，返回 (lat, lon)，单位为度。"""
    if t == 0.0:
        return (float(lat1), normalize_lon(lon1))
    if t == 1.0:
        return (float(lat2), normalize_lon(lon2))

    sigma = central_angle(lat1, lon1, lat2, lon2)
    if sigma <= _COINCIDENT_EPS:  # 两点重合
        return (float(lat1), normalize_lon(lon1))

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    lam1 = math.radians(lon1)
    dlam = math.radians(normalize_lon(lon2 - lon1))
    antipodal = math.pi - sigma <= _ANTIPODAL_EPS

    sin_phi1 = math.sin(phi1)
    if sin_phi1 == 1.0 or sin_phi1 == -1.0:
        # 起点正好在极点：大圆即 p2 所在经线（方位角在极点退化，直接构造）。
        # 若 p2 是另一个极点（对跖），按约定走 p1 经线。
        lon_path = normalize_lon(lon1) if antipodal else normalize_lon(lon2)
        delta = t * (math.pi if antipodal else sigma)
        return (90.0 * sin_phi1 - sin_phi1 * math.degrees(delta), lon_path)

    if antipodal:
        # 对跖点：约定沿 p1 所在半球的经线走（过极点），弧长按 pi 参数化。
        theta = 0.0 if lat1 >= 0.0 else math.pi
        delta = t * math.pi
    else:
        theta = math.atan2(
            math.sin(dlam) * math.cos(phi2),
            math.cos(phi1) * math.sin(phi2)
            - math.sin(phi1) * math.cos(phi2) * math.cos(dlam),
        )
        delta = t * sigma

    sin_phi = (math.sin(phi1) * math.cos(delta)
               + math.cos(phi1) * math.sin(delta) * math.cos(theta))
    phi = math.asin(max(-1.0, min(1.0, sin_phi)))
    dlam_out = math.atan2(
        math.sin(theta) * math.sin(delta) * math.cos(phi1),
        math.cos(delta) - math.sin(phi1) * sin_phi,
    )
    return (math.degrees(phi), normalize_lon(math.degrees(lam1 + dlam_out)))


def slerp_path(lat1, lon1, lat2, lon2, n):
    """等分插值路径，返回 n+1 个 (lat, lon)（含端点）。"""
    if n < 1:
        raise ValueError("n must be >= 1")
    return [slerp(lat1, lon1, lat2, lon2, i / n) for i in range(n + 1)]
