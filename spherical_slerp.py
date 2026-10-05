"""球面坐标插值库（仅 Python 标准库）。

在球面上沿大圆做球面线性插值（slerp）：给定两端点的经纬度（度）与参数
t（0..1），返回大圆上按圆心角匀速分布的中间点。经度跨越 ±180° 分界时
自动取短弧（因为中心角由 haversine 公式给出，恒为 [0, pi] 的最短弧）。

两套实现：
  slerp            —— 球面三角法（本库主实现）：haversine 求中心角，
                      球面三角求初始方位角，再沿大圆"给距离与方位求终点"。
  slerp_cartesian  —— 笛卡尔参考实现：经纬度转单位向量，三维向量 slerp，
                      再转回经纬度。用于对拍验证。

退化情形的确定性约定（两套实现一致）：
  * 两点重合（中心角 < _COINCIDENT_EPS）：直接返回起点。
  * 对跖点（pi - 中心角 < _ANTIPODAL_EPS）：大圆不唯一，约定走"过极点"
    的大圆——球面法取初始方位角 0（正北）；笛卡尔法取 z 轴在垂直于
    起点向量方向上的投影作为插值副轴（起点在极点时改用 x 轴方向）。
    两种约定数学上重合，且 t=1 时必然精确落在终点（对跖点与方位无关）。
"""

import math

__all__ = [
    "slerp",
    "slerp_cartesian",
    "central_angle",
    "initial_bearing",
    "destination",
    "to_cartesian",
    "to_spherical",
    "angular_error_deg",
    "EARTH_RADIUS_KM",
]

EARTH_RADIUS_KM = 6371.0088  # IUGG 地球平均半径

_COINCIDENT_EPS = 1e-15   # 弧度；中心角小于此值视为两点重合
_ANTIPODAL_EPS = 1e-12    # 弧度；中心角与 pi 之差小于此值视为对跖点


def _norm_lon(lon):
    """经度归一化到 [-180, 180)。"""
    return (lon + 180.0) % 360.0 - 180.0


def to_cartesian(lat_deg, lon_deg):
    """球面坐标（度）转单位笛卡尔向量 (x, y, z)。"""
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    c = math.cos(lat)
    return (c * math.cos(lon), c * math.sin(lon), math.sin(lat))


def to_spherical(x, y, z):
    """单位笛卡尔向量转球面坐标 (lat_deg, lon_deg)，经度归一化。"""
    hyp = math.hypot(x, y)
    lat = math.degrees(math.atan2(z, hyp))
    lon = _norm_lon(math.degrees(math.atan2(y, x)))
    return (lat, lon)


def central_angle(lat1_deg, lon1_deg, lat2_deg, lon2_deg):
    """haversine 公式求两点间中心角（弧度，[0, pi]，恒为最短弧）。"""
    phi1 = math.radians(lat1_deg)
    phi2 = math.radians(lat2_deg)
    dphi = phi2 - phi1
    dlam = math.radians(lon2_deg - lon1_deg)
    h = (math.sin(dphi * 0.5) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlam * 0.5) ** 2)
    h = min(1.0, h)  # 防浮点越界
    return 2.0 * math.atan2(math.sqrt(h), math.sqrt(1.0 - h))


def initial_bearing(lat1_deg, lon1_deg, lat2_deg, lon2_deg):
    """起点到终点的大圆初始方位角（弧度，0=正北，顺时针）。"""
    phi1 = math.radians(lat1_deg)
    phi2 = math.radians(lat2_deg)
    dlam = math.radians(lon2_deg - lon1_deg)
    y = math.sin(dlam) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    return math.atan2(y, x)


def destination(lat_deg, lon_deg, bearing_rad, dist_rad):
    """从 (lat, lon) 沿方位角 bearing 走球面距离 dist（弧度）后的经纬度。"""
    phi1 = math.radians(lat_deg)
    lam1 = math.radians(lon_deg)
    sp1 = math.sin(phi1)
    cp1 = math.cos(phi1)
    cb = math.cos(bearing_rad)
    sb = math.sin(bearing_rad)
    sd = math.sin(dist_rad)
    cd = math.cos(dist_rad)
    phi2 = math.asin(sp1 * cd + cp1 * sd * cb)
    # 经度公式分子分母原都含 cos(phi1)（极点处为 0，造成 0/0），
    # 用 sin(phi2) 展开后约去 cos(phi1)，得到极点处仍然确定的稳定形式：
    #   y = sin(bearing)*sin(d)
    #   x = cos(phi1)*cos(d) - sin(phi1)*sin(d)*cos(bearing)
    lam2 = lam1 + math.atan2(
        sb * sd,
        cp1 * cd - sp1 * sd * cb,
    )
    return (math.degrees(phi2), _norm_lon(math.degrees(lam2)))


def slerp(lat1_deg, lon1_deg, lat2_deg, lon2_deg, t):
    """球面三角法大圆插值（本库主实现）。

    lat/lon 单位为度，t=0 返回起点，t=1 返回终点。
    """
    delta = central_angle(lat1_deg, lon1_deg, lat2_deg, lon2_deg)
    if delta < _COINCIDENT_EPS:
        # 两点重合：结果唯一，直接返回起点
        return (lat1_deg, _norm_lon(lon1_deg))
    if math.pi - delta < _ANTIPODAL_EPS:
        # 对跖点：大圆不唯一，确定性约定取初始方位角 0（正北，过极点）
        bearing = 0.0
    else:
        bearing = initial_bearing(lat1_deg, lon1_deg, lat2_deg, lon2_deg)
    return destination(lat1_deg, lon1_deg, bearing, t * delta)


def slerp_cartesian(lat1_deg, lon1_deg, lat2_deg, lon2_deg, t):
    """笛卡尔参考实现：单位向量 slerp，用于对拍。"""
    p1 = to_cartesian(lat1_deg, lon1_deg)
    p2 = to_cartesian(lat2_deg, lon2_deg)
    dot = max(-1.0, min(1.0, p1[0] * p2[0] + p1[1] * p2[1] + p1[2] * p2[2]))
    # 用 atan2(|p1 x p2|, p1.p2) 求夹角，数值稳定
    cx = p1[1] * p2[2] - p1[2] * p2[1]
    cy = p1[2] * p2[0] - p1[0] * p2[2]
    cz = p1[0] * p2[1] - p1[1] * p2[0]
    delta = math.atan2(math.sqrt(cx * cx + cy * cy + cz * cz), dot)
    if delta < _COINCIDENT_EPS:
        return to_spherical(*p1)
    if math.pi - delta < _ANTIPODAL_EPS:
        # 对跖点：取 z 轴垂直于 p1 的分量作副轴（与球面法"正北"约定一致）；
        # p1 在极点附近时改用 x 轴的垂直分量。
        ax = (0.0, 0.0, 1.0) if abs(p1[2]) < 0.9 else (1.0, 0.0, 0.0)
        d = p1[0] * ax[0] + p1[1] * ax[1] + p1[2] * ax[2]
        nx, ny, nz = ax[0] - d * p1[0], ax[1] - d * p1[1], ax[2] - d * p1[2]
        n = math.sqrt(nx * nx + ny * ny + nz * nz)
        nx, ny, nz = nx / n, ny / n, nz / n
        a = t * math.pi
        ca, sa = math.cos(a), math.sin(a)
        return to_spherical(
            p1[0] * ca + nx * sa,
            p1[1] * ca + ny * sa,
            p1[2] * ca + nz * sa,
        )
    sd = math.sin(delta)
    a = math.sin((1.0 - t) * delta) / sd
    b = math.sin(t * delta) / sd
    return to_spherical(
        a * p1[0] + b * p2[0],
        a * p1[1] + b * p2[1],
        a * p1[2] + b * p2[2],
    )


def angular_error_deg(a, b):
    """两个 (lat, lon) 结果之间的角误差（度）。"""
    return math.degrees(central_angle(a[0], a[1], b[0], b[1]))
