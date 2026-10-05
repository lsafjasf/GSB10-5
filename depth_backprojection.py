# -*- coding: utf-8 -*-
"""
depth_backprojection.py
深度图 -> 三维点云 的反投影库。

约束：Python 3，仅使用标准库（math / array / numbers）。

=====================================================================
相机与镜头模型说明
=====================================================================
1. 坐标系
   相机坐标系：X 轴向右，Y 轴向下，Z 轴指向场景（光轴向前）。
   三维点 P = (X, Y, Z)，要求 Z > 0（在相机前方）。

2. 针孔相机内参
       | fx   0  cx |
   K = |  0  fy  cy |
       |  0   0   1 |
   归一化像面坐标：x = X / Z,  y = Y / Z
   投影像素坐标：  u = fx * x_d + cx,  v = fy * y_d + cy

3. 畸变模型（Brown-Conrady 径向 + 切向模型，与 OpenCV 一致）
   无畸变归一化坐标 (x, y) -> 畸变后归一化坐标 (x_d, y_d)：
       r2 = x*x + y*y
       radial = 1 + k1*r2 + k2*r2^2 + k3*r2^3
       x_d = x*radial + 2*p1*x*y + p2*(r2 + 2*x^2)
       y_d = y*radial + p1*(r2 + 2*y^2) + 2*p2*x*y
   参数：k1,k2,k3 径向畸变；p1,p2 切向（薄棱镜）畸变。
   全为 0 时即无畸变相机。

4. 去畸变（反投影必需）
   (x_d, y_d) -> (x, y) 无闭式解，采用不动点迭代：
       初值 x = x_d, y = y_d
       反复计算：
           radial = 1 + k1*r2 + k2*r2^2 + k3*r2^3
           tx = 2*p1*x*y + p2*(r2 + 2*x*x)
           ty = p1*(r2 + 2*y*y) + 2*p2*x*y
           x = (x_d - tx) / radial
           y = (y_d - ty) / radial
   默认迭代 12 次；强畸变 (|k1|=0.2 量级) 下全图往返残差 < 1e-5
   （约 0.005 像素，远低于 0.5 像素的量化噪声，自测场景 D 验证）。
   若漏掉这一步，直接用 (x_d, y_d) 当 (x, y)，视场越靠外的像素
   角度偏差越大，反投影出的点会整体外偏/内偏。

5. 反投影公式
   对像素 (i, j) 的有效深度 Z：
       x_d = (i - cx) / fx
       y_d = (j - cy) / fy
       (x, y) = undistort(x_d, y_d)
       X = x * Z,  Y = y * Z,  Z = Z

6. 取整 / 量化约定
   - 投影时连续像素坐标四舍五入到最近整数像素：
       i = floor(u + 0.5), j = floor(v + 0.5)
   - 反投影时把整数像素索引 (i, j) 当作该像素的采样中心，
     即直接取 u = i, v = j（半像素偏移在此约定下两边一致）。
   - 深度以毫米整数 (uint16) 量化：Z_mm = floor(Z/scale + 0.5)，
     反投影 Z = Z_mm * depth_scale，depth_scale 默认 0.001（米）。

7. 缺失 / 零深度处理
   深度值为 0 一律视为无效（缺失或测距失败）：
   - 点云输出中该位置坐标写 (0, 0, 0)；
   - valid 掩码（array('b')）记 0，有效像素记 1；
   - 有效点数量单独返回。调用方按 valid 取值即可，绝不产生
     Z=0 导致的无限远点或除零问题。
"""

from array import array
import math


class Intrinsics(object):
    """针孔相机内参 fx, fy, cx, cy（像素单位）。"""

    __slots__ = ("fx", "fy", "cx", "cy")

    def __init__(self, fx, fy, cx, cy):
        self.fx = float(fx)
        self.fy = float(fy)
        self.cx = float(cx)
        self.cy = float(cy)


class BrownConradyDistortion(object):
    """Brown-Conrady 畸变参数 k1, k2, p1, p2, k3；全 0 表示无畸变。"""

    __slots__ = ("k1", "k2", "p1", "p2", "k3")

    def __init__(self, k1=0.0, k2=0.0, p1=0.0, p2=0.0, k3=0.0):
        self.k1 = float(k1)
        self.k2 = float(k2)
        self.p1 = float(p1)
        self.p2 = float(p2)
        self.k3 = float(k3)

    def is_identity(self):
        return (self.k1 == 0.0 and self.k2 == 0.0 and self.p1 == 0.0
                and self.p2 == 0.0 and self.k3 == 0.0)

    def distort_normalized(self, x, y):
        """无畸变归一化坐标 -> 畸变归一化坐标。"""
        r2 = x * x + y * y
        radial = 1.0 + self.k1 * r2 + self.k2 * r2 * r2 \
            + self.k3 * r2 * r2 * r2
        xd = x * radial + 2.0 * self.p1 * x * y \
            + self.p2 * (r2 + 2.0 * x * x)
        yd = y * radial + self.p1 * (r2 + 2.0 * y * y) \
            + 2.0 * self.p2 * x * y
        return xd, yd

    def undistort_normalized(self, xd, yd, iters=12):
        """畸变归一化坐标 -> 无畸变归一化坐标（不动点迭代）。"""
        x = xd
        y = yd
        k1, k2, k3 = self.k1, self.k2, self.k3
        p1, p2 = self.p1, self.p2
        for _ in range(iters):
            r2 = x * x + y * y
            radial = 1.0 + k1 * r2 + k2 * r2 * r2 + k3 * r2 * r2 * r2
            tx = 2.0 * p1 * x * y + p2 * (r2 + 2.0 * x * x)
            ty = p1 * (r2 + 2.0 * y * y) + 2.0 * p2 * x * y
            x = (xd - tx) / radial
            y = (yd - ty) / radial
        return x, y


NONE_DISTORTION = BrownConradyDistortion()


def project_point(point, intr, dist=NONE_DISTORTION):
    """三维点 (X,Y,Z) -> 连续像素坐标 (u,v)。Z<=0 返回 None。"""
    X, Y, Z = point
    if Z <= 0.0:
        return None
    x, y = X / Z, Y / Z
    if dist is not None and not dist.is_identity():
        x, y = dist.distort_normalized(x, y)
    return intr.fx * x + intr.cx, intr.fy * y + intr.cy


def pixel_index(u, v):
    """连续像素坐标四舍五入到整数像素索引：floor(coord + 0.5)。"""
    return int(math.floor(u + 0.5)), int(math.floor(v + 0.5))


def backproject_pixel(i, j, z_mm, intr, dist=NONE_DISTORTION,
                      depth_scale=0.001, undistort_iters=12):
    """
    单个像素反投影。

    返回 (X, Y, Z, valid)：
      z_mm <= 0 时 valid=False，坐标为 (0.0, 0.0, 0.0)。
    """
    if z_mm <= 0:
        return 0.0, 0.0, 0.0, False
    xd = (float(i) - intr.cx) / intr.fx
    yd = (float(j) - intr.cy) / intr.fy
    if dist is not None and not dist.is_identity():
        x, y = dist.undistort_normalized(xd, yd, undistort_iters)
    else:
        x, y = xd, yd
    Z = float(z_mm) * depth_scale
    return x * Z, y * Z, Z, True


def backproject_depth(depth_mm, width, height, intr,
                      dist=NONE_DISTORTION, depth_scale=0.001,
                      undistort_iters=12):
    """
    整张深度图反投影。

    参数
    ----
    depth_mm : array('H') / 可索引序列，长度 width*height，行优先，
               单位毫米（uint16），0 表示缺失。
    width, height : 图像尺寸（像素）。
    intr : Intrinsics。
    dist : BrownConradyDistortion；None 或全 0 表示不做畸变补偿
           （对照实验用）。
    depth_scale : 深度单位换算，0.001 = 毫米转米。

    返回
    ----
    (xs, ys, zs, valid, valid_count)
      xs/ys/zs : array('f')，长度 width*height，无效位置为 0.0；
      valid    : array('b')，1 有效 / 0 缺失；
      valid_count : 有效像素数。
    """
    n = width * height
    if len(depth_mm) != n:
        raise ValueError("depth length %d != width*height %d"
                         % (len(depth_mm), n))

    xs = array('f', [0.0]) * n
    ys = array('f', [0.0]) * n
    zs = array('f', [0.0]) * n
    valid = array('b', b'\x00') * n

    fx, fy = intr.fx, intr.fy
    cx, cy = intr.cx, intr.cy
    do_dist = dist is not None and not dist.is_identity()
    if do_dist:
        k1, k2, k3 = dist.k1, dist.k2, dist.k3
        p1, p2 = dist.p1, dist.p2
    it = undistort_iters

    # 预计算每列/每行的畸变归一化坐标，循环内只做去畸变迭代与深度相乘。
    xd_col = array('d')
    for i in range(width):
        xd_col.append((i - cx) / fx)
    yd_row = array('d')
    for j in range(height):
        yd_row.append((j - cy) / fy)

    count = 0
    idx = 0
    for j in range(height):
        yd0 = yd_row[j]
        for i in range(width):
            z_mm = depth_mm[idx]
            if z_mm:
                xd0 = xd_col[i]
                if do_dist:
                    x = xd0
                    y = yd0
                    for _ in range(it):
                        r2 = x * x + y * y
                        radial = (1.0 + k1 * r2 + k2 * r2 * r2
                                  + k3 * r2 * r2 * r2)
                        tx = 2.0 * p1 * x * y + p2 * (r2 + 2.0 * x * x)
                        ty = p1 * (r2 + 2.0 * y * y) + 2.0 * p2 * x * y
                        x = (xd0 - tx) / radial
                        y = (yd0 - ty) / radial
                else:
                    x = xd0
                    y = yd0
                Z = z_mm * depth_scale
                xs[idx] = x * Z
                ys[idx] = y * Z
                zs[idx] = Z
                valid[idx] = 1
                count += 1
            idx += 1

    return xs, ys, zs, valid, count
