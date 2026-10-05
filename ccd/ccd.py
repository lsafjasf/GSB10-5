"""ccd.py — 连续碰撞检测（CCD）库，扫掠解析法（swept analytic test）。

形状：
    Circle —— 匀速直线运动的圆
    Box    —— 匀速直线运动的轴对齐矩形（AABB，可视为无限质量墙体）

一帧 [0, dt] 内位置 p(t) = p0 + v * (t * dt)，t 为归一化时间 ∈ [0,1]。
检测函数返回 (t, kind)，t=0 表示帧首，t=1 表示帧末；返回 None 表示无接触。

事件类型：
    COLLIDE 穿透性碰撞（法向相对速度朝彼此，会产生响应）
    TOUCH   擦边接触（恰好相切、零穿透：报告但不算碰撞、不产生速度响应）
    OVERLAP 帧起始时两物体已重叠（退化情形）

simulate() 在一帧内反复寻找最早 TOI、推进、做弹性响应，
从而按时间顺序处理一帧内的多次碰撞。
"""

import math

COLLIDE = "collide"
TOUCH = "touch"
OVERLAP = "overlap"

# 相切判别用的相对容差。越大越稳（数值噪声不会把擦边算成微碰撞），
# 但会把“极浅穿透”也归为擦边。详见 README「精度与代价」。
EPS = 1e-9


class Circle:
    __slots__ = ("x", "y", "vx", "vy", "r")

    def __init__(self, x, y, vx, vy, r):
        self.x, self.y, self.vx, self.vy, self.r = x, y, vx, vy, r

    def advance(self, dt):
        self.x += self.vx * dt
        self.y += self.vy * dt

    def copy(self):
        return Circle(self.x, self.y, self.vx, self.vy, self.r)


class Box:
    __slots__ = ("x", "y", "hx", "hy", "vx", "vy")

    def __init__(self, x, y, hx, hy, vx=0.0, vy=0.0):
        self.x, self.y, self.hx, self.hy = x, y, hx, hy
        self.vx, self.vy = vx, vy

    def advance(self, dt):
        self.x += self.vx * dt
        self.y += self.vy * dt

    def copy(self):
        return Box(self.x, self.y, self.hx, self.hy, self.vx, self.vy)


def sweep_circle_circle(a, b, dt=1.0):
    """圆-圆扫掠：解 |r0 + t*u| = R 的二次方程，返回最早接触 (t, kind)。

    r0 = 帧首相对位置，u = 相对速度 * dt，R = 两半径之和。
    """
    rx = a.x - b.x
    ry = a.y - b.y
    ux = (a.vx - b.vx) * dt
    uy = (a.vy - b.vy) * dt
    radius_sum = a.r + b.r
    radius_sum2 = radius_sum * radius_sum

    aa = ux * ux + uy * uy
    bb = 2.0 * (rx * ux + ry * uy)
    cc = rx * rx + ry * ry - radius_sum2  # g(t) = aa t² + bb t + cc

    tol_c = EPS * max(radius_sum2, 1.0)

    if cc < -tol_c:
        return (0.0, OVERLAP)
    if cc <= tol_c:
        # 帧首恰好接触：只有正在闭合时才视为新事件，分离中的接触忽略
        if bb < 0.0:
            return (0.0, COLLIDE)
        return None
    if aa == 0.0:
        return None  # 无相对运动且当前分离

    disc = bb * bb - 4.0 * aa * cc
    tol_d = EPS * max(bb * bb, abs(4.0 * aa * cc), 1.0)

    if disc < -tol_d:
        return None  # 最近距离仍大于 R：完全错过

    if abs(disc) <= tol_d:
        t = -bb / (2.0 * aa)
        if 0.0 <= t <= 1.0 and bb < 0.0:
            return (t, TOUCH)  # 重根：刚好相切
        return None

    t = (-bb - math.sqrt(disc)) / (2.0 * aa)  # 小根 = 进入时刻
    if 0.0 <= t <= 1.0:
        return (t, COLLIDE)
    return None


def sweep_circle_box(c, b, dt=1.0):
    """圆-AABB 扫掠：相对射线 vs 盒膨胀半径后的 Minkowski 和。

    Minkowski 和 = 面区域（用 slab 射线法）+ 4 个圆角（用圆-点扫掠）。
    两类候选取最早者，保证角部结果精确，而非膨胀盒式的保守近似。
    """
    ux = (c.vx - b.vx) * dt
    uy = (c.vy - b.vy) * dt
    px = c.x - b.x
    py = c.y - b.y
    ex = b.hx + c.r  # 膨胀半宽（含圆角半径）
    ey = b.hy + c.r

    # 帧首是否已接触/重叠：圆心到盒最近点的距离
    qx = min(max(px, -b.hx), b.hx)
    qy = min(max(py, -b.hy), b.hy)
    d2 = (px - qx) ** 2 + (py - qy) ** 2 - c.r * c.r
    tol_c = EPS * max(c.r * c.r, 1.0)
    if d2 < -tol_c:
        return (0.0, OVERLAP)
    if d2 <= tol_c:
        # 帧首接触：朝盒运动才算新事件
        if px * ux + py * uy < 0.0:
            return (0.0, COLLIDE)
        return None

    # slab 法求进入膨胀矩形的时刻
    t_enter, t_exit = 0.0, 1.0
    for p, u, e in ((px, ux, ex), (py, uy, ey)):
        if u == 0.0:
            if p < -e or p > e:
                break
            continue
        t1 = (-e - p) / u
        t2 = (e - p) / u
        if t1 > t2:
            t1, t2 = t2, t1
        t_enter = max(t_enter, t1)
        t_exit = min(t_exit, t2)
        if t_enter > t_exit:
            break
    else:
        candidates = []
        hx_hit = px + ux * t_enter
        hy_hit = py + uy * t_enter
        # 进入点落在面区域（非角部方块）才是有效面命中
        if 0.0 <= t_enter <= 1.0 and (abs(hx_hit) <= b.hx or abs(hy_hit) <= b.hy):
            candidates.append((t_enter, COLLIDE))
        # 圆角：圆 vs 4 个角点（零半径圆，带盒的速度）
        for sx in (-1.0, 1.0):
            for sy in (-1.0, 1.0):
                corner = Circle(b.x + sx * b.hx, b.y + sy * b.hy,
                                b.vx, b.vy, 0.0)
                ev = sweep_circle_circle(c, corner, dt)
                if ev is not None and ev[1] is not OVERLAP:
                    candidates.append(ev)
        if candidates:
            return min(candidates, key=lambda e: e[0])
    return None


def _sweep_pair(a, b, dt):
    if isinstance(a, Circle) and isinstance(b, Circle):
        return sweep_circle_circle(a, b, dt)
    if isinstance(a, Circle) and isinstance(b, Box):
        return sweep_circle_box(a, b, dt)
    if isinstance(a, Box) and isinstance(b, Circle):
        return sweep_circle_box(b, a, dt)
    return None  # Box-Box 不处理


def _separated(a, b):
    if isinstance(a, Circle) and isinstance(b, Circle):
        r = a.r + b.r
        return (a.x - b.x) ** 2 + (a.y - b.y) ** 2 > r * r * (1.0 + EPS)
    if isinstance(a, Box) or isinstance(b, Box):
        c, box = (a, b) if isinstance(a, Circle) else (b, a)
        qx = min(max(c.x, box.x - box.hx), box.x + box.hx)
        qy = min(max(c.y, box.y - box.hy), box.y + box.hy)
        return (c.x - qx) ** 2 + (c.y - qy) ** 2 > c.r * c.r * (1.0 + EPS)
    return True


def _resolve(a, b):
    """弹性响应：圆-圆等质量交换法向分量；圆-墙圆被镜面反射。"""
    if isinstance(a, Circle) and isinstance(b, Circle):
        dx, dy = a.x - b.x, a.y - b.y
        d = math.hypot(dx, dy) or 1.0
        nx, ny = dx / d, dy / d
        va = a.vx * nx + a.vy * ny
        vb = b.vx * nx + b.vy * ny
        if va - vb >= 0.0:
            return  # 已在分离（可能是同一时刻的重复事件）
        a.vx += (vb - va) * nx
        a.vy += (vb - va) * ny
        b.vx += (va - vb) * nx
        b.vy += (va - vb) * ny
        return
    c, box = (a, b) if isinstance(a, Circle) else (b, a)
    qx = min(max(c.x, box.x - box.hx), box.x + box.hx)
    qy = min(max(c.y, box.y - box.hy), box.y + box.hy)
    dx, dy = c.x - qx, c.y - qy
    d = math.hypot(dx, dy) or 1.0
    nx, ny = dx / d, dy / d
    rvx, rvy = c.vx - box.vx, c.vy - box.vy
    vn = rvx * nx + rvy * ny
    if vn >= 0.0:
        return
    c.vx -= 2.0 * vn * nx
    c.vy -= 2.0 * vn * ny


def simulate(bodies, dt=1.0, max_events=10000):
    """在一帧 dt 内按时间顺序处理多次碰撞。

    返回 (events, bodies)。
    events 每项 = (绝对时刻, kind, i, j)，已按时刻升序排列。
    bodies 为推进/响应后的副本。
    """
    bodies = [b.copy() for b in bodies]
    n = len(bodies)
    events = []
    t_now = 0.0
    ignored = set()  # 已报告的擦边/重叠对，分离前不再重复报告

    for _ in range(max_events):
        rem = dt - t_now
        if rem <= 0.0:
            break
        for pair in list(ignored):
            i, j = pair
            if _separated(bodies[i], bodies[j]):
                ignored.discard(pair)

        best_t = None
        best = []
        for i in range(n):
            for j in range(i + 1, n):
                if (i, j) in ignored:
                    continue
                ev = _sweep_pair(bodies[i], bodies[j], rem)
                if ev is None:
                    continue
                t_abs = t_now + ev[0] * rem
                if best_t is None or t_abs < best_t - 1e-12:
                    best_t, best = t_abs, [(i, j, ev[1])]
                elif t_abs <= best_t + 1e-12:
                    best.append((i, j, ev[1]))

        if best_t is None:
            break

        step = best_t - t_now
        for body in bodies:
            body.advance(step)
        t_now = best_t

        for i, j, kind in best:
            events.append((t_now, kind, i, j))
            if kind is COLLIDE:
                _resolve(bodies[i], bodies[j])
            else:
                ignored.add((i, j))

    return events, bodies
