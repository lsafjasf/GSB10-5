"""笛卡尔（3D 单位向量）坐标下的 slerp 参考实现，仅用于对拍。

把经纬度转单位向量，在 R^3 中做标准球面线性插值，再转回经纬度。
对跖点约定与 slerp.py 完全一致：沿 p1 所在半球的经线走。
"""

import math

from slerp import normalize_lon


def to_unit_vector(lat, lon):
    phi = math.radians(lat)
    lam = math.radians(lon)
    cp = math.cos(phi)
    return (cp * math.cos(lam), cp * math.sin(lam), math.sin(phi))


def to_latlon(x, y, z):
    norm = math.sqrt(x * x + y * y + z * z)
    x /= norm
    y /= norm
    z /= norm
    lat = math.degrees(math.asin(max(-1.0, min(1.0, z))))
    lon = math.degrees(math.atan2(y, x))
    return (lat, normalize_lon(lon))


def ref_slerp(lat1, lon1, lat2, lon2, t):
    if t == 0.0:
        return (float(lat1), normalize_lon(lon1))
    if t == 1.0:
        return (float(lat2), normalize_lon(lon2))

    v1 = to_unit_vector(lat1, lon1)
    v2 = to_unit_vector(lat2, lon2)
    dot = min(1.0, max(-1.0, v1[0] * v2[0] + v1[1] * v2[1] + v1[2] * v2[2]))
    omega = math.acos(dot)

    if omega == 0.0:  # 重合
        return (float(lat1), normalize_lon(lon1))

    if math.pi - omega <= 1e-9:
        # 对跖点：与 slerp.py 相同的约定——沿 p1 所在半球的经线走。
        # w 为 v1 处沿约定路径的切向量（lat1>=0 朝北，否则朝南；
        # p1 正好在极点时沿 lon1 经线向赤道）。
        phi1 = math.radians(lat1)
        lam1 = math.radians(lon1)
        sp = math.sin(phi1)
        if sp == 1.0 or sp == -1.0:
            w = (math.cos(lam1), math.sin(lam1), 0.0)
        else:
            sign = 1.0 if lat1 >= 0.0 else -1.0
            w = (-sign * sp * math.cos(lam1),
                 -sign * sp * math.sin(lam1),
                 sign * math.cos(phi1))
        angle = math.pi * t
        v = tuple(math.cos(angle) * v1[k] + math.sin(angle) * w[k] for k in range(3))
        return to_latlon(*v)

    s1 = math.sin((1.0 - t) * omega) / math.sin(omega)
    s2 = math.sin(t * omega) / math.sin(omega)
    v = tuple(s1 * v1[k] + s2 * v2[k] for k in range(3))
    return to_latlon(*v)


def angular_error(a, b):
    """两组 (lat, lon) 的圆心角误差（弧度）。"""
    va = to_unit_vector(*a)
    vb = to_unit_vector(*b)
    dot = min(1.0, max(-1.0, sum(va[k] * vb[k] for k in range(3))))
    return math.acos(dot)
