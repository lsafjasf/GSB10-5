"""连续碰撞检测（CCD）库：扫掠圆 vs 圆 / 线段，闭式解析求根。

检测方法：扫掠（swept collision）+ 闭式求根（非细分迭代）。
- 圆 vs 圆：相对运动 p(t) = p0 + v*t，解二次方程 |p(t)-c|^2 = (r1+r2)^2。
- 圆 vs 线段：线段膨胀为胶囊 = 两个端点圆 + 两侧偏移直线；
  端点用圆-圆扫掠，侧边解一次方程 (p(t)-a)·n = ±r 并校验投影区间。

精度与代价：闭式根，单次查询 O(1)，误差仅来自浮点舍入（约 1e-12 量级）；
相比二分/细分求根无需迭代，代价固定，但对恰相切情形需用容差分类。
"""

import math
from dataclasses import dataclass, field

HIT = "hit"          # 穿透性首次接触（真实碰撞）
TANGENT = "tangent"  # 恰好相切（擦边，不算碰撞，但必须报告）
OVERLAP = "overlap"  # 初始已重叠

_REL_TOL = 1e-9


def sweep_point_circle(px, py, vx, vy, cx, cy, radius, tmax):
    """扫掠点（膨胀半径 radius）vs 静止圆心 (cx, cy)。

    返回 (t, kind) 或 None，t ∈ [0, tmax]。
    """
    rx = px - cx
    ry = py - cy
    c = rx * rx + ry * ry - radius * radius
    tol_c = _REL_TOL * max(1.0, radius * radius)
    a = vx * vx + vy * vy
    b = 2.0 * (vx * rx + vy * ry)
    if c < -tol_c:
        return (0.0, OVERLAP)
    if a == 0.0:
        return None
    if c <= tol_c:
        # 已在接触容差内：接近中 -> 立即命中；远离/沿切向滑动 -> 无新事件
        return (0.0, HIT) if b < 0.0 else None
    if b >= 0.0:
        return None  # 分离中
    disc = b * b - 4.0 * a * c
    scale = b * b + 4.0 * a * c
    tol_d = _REL_TOL * max(scale, 1.0)
    if disc < -tol_d:
        return None
    if disc <= tol_d:
        # 判别式≈0：最近距离恰好等于半径和 -> 擦边相切
        t = -b / (2.0 * a)
        if 0.0 <= t <= tmax:
            return (t, TANGENT)
        return None
    t = (-b - math.sqrt(disc)) / (2.0 * a)
    if 0.0 <= t <= tmax:
        return (t, HIT)
    return None


def sweep_circle_circle(px, py, vx, vy, r1, cx, cy, r2, tmax):
    """运动圆 (p, v, r1) vs 静止圆 (c, r2)。"""
    return sweep_point_circle(px, py, vx, vy, cx, cy, r1 + r2, tmax)


def _point_segment_dist2(px, py, ax, ay, bx, by):
    dx = bx - ax
    dy = by - ay
    ll = dx * dx + dy * dy
    if ll == 0.0:
        return (px - ax) ** 2 + (py - ay) ** 2
    u = ((px - ax) * dx + (py - ay) * dy) / ll
    u = 0.0 if u < 0.0 else (1.0 if u > 1.0 else u)
    qx = ax + u * dx
    qy = ay + u * dy
    return (px - qx) ** 2 + (py - qy) ** 2


def sweep_circle_segment(px, py, vx, vy, r, ax, ay, bx, by, tmax):
    """运动圆 (p, v, r) vs 静止线段（零厚度墙，按胶囊处理）。

    返回 (t, kind, nx, ny) 或 None，法线 (nx, ny) 指向来圆一侧。
    """
    best = None

    def consider(res, nx, ny):
        nonlocal best
        if res is None:
            return
        t, kind = res
        if best is None or t < best[0]:
            best = (t, kind, nx, ny)

    tol_d = _REL_TOL * max(1.0, r)
    d2 = _point_segment_dist2(px, py, ax, ay, bx, by)
    if d2 < (r - tol_d) ** 2:
        return (0.0, OVERLAP, 0.0, 0.0)

    # 端点圆
    for cx, cy in ((ax, ay), (bx, by)):
        res = sweep_point_circle(px, py, vx, vy, cx, cy, r, tmax)
        if res is not None:
            t, kind = res
            hx = px + vx * t
            hy = py + vy * t
            d = math.hypot(hx - cx, hy - cy)
            if d > 0.0:
                consider((t, kind), (hx - cx) / d, (hy - cy) / d)

    # 侧边（偏移直线，再校验投影区间）
    dx = bx - ax
    dy = by - ay
    length = math.hypot(dx, dy)
    if length > 0.0:
        ux = dx / length
        uy = dy / length
        nx = -uy
        ny = ux
        dist0 = (px - ax) * nx + (py - ay) * ny
        denom = vx * nx + vy * ny
        side = r if dist0 >= 0.0 else -r
        if abs(denom) <= _REL_TOL * max(1.0, abs(vx) + abs(vy)):
            # 与侧边平行滑动：间距恰好为 r -> 持续擦边相切
            if abs(abs(dist0) - r) <= tol_d:
                sgn = 1.0 if dist0 >= 0.0 else -1.0
                consider((0.0, TANGENT), sgn * nx, sgn * ny)
        else:
            t = (side - dist0) / denom
            if 0.0 <= t <= tmax:
                # 已在接触面上时，仅在接近（将穿透）时命中；正在分离不算
                tol_t = _REL_TOL * max(1.0, tmax)
                if t <= tol_t and side * denom >= 0.0:
                    pass
                else:
                    hx = px + vx * t
                    hy = py + vy * t
                    u = (hx - ax) * ux + (hy - ay) * uy
                    if 0.0 <= u <= length:
                        sgn = 1.0 if side > 0.0 else -1.0
                        consider((t, HIT), sgn * nx, sgn * ny)
    return best


@dataclass
class Body:
    x: float
    y: float
    vx: float
    vy: float
    r: float
    name: str = "body"


@dataclass
class StaticCircle:
    x: float
    y: float
    r: float
    name: str = "circle"


@dataclass
class Segment:
    ax: float
    ay: float
    bx: float
    by: float
    name: str = "wall"


@dataclass
class Event:
    t: float       # 帧内绝对时刻
    kind: str      # 'collision' | 'tangent' | 'overlap'
    body: str
    other: str
    nx: float = 0.0
    ny: float = 0.0

    def __str__(self):
        return (f"t={self.t:.9f}  {self.kind:9s}  {self.body} vs {self.other}"
                f"  n=({self.nx:+.3f},{self.ny:+.3f})")


class World:
    """最小世界：若干运动圆 + 静止圆/线段障碍。

    step(dt) 在一帧内按时间顺序处理所有碰撞事件：
    找到最早事件 -> 推进到该时刻 -> 记录事件 -> 碰撞则反弹 -> 继续扫描。
    相切/重叠事件只报告一次（不改变运动），避免同对重复触发。
    """

    def __init__(self):
        self.bodies = []
        self.circles = []
        self.segments = []

    def add_body(self, *args, **kw):
        b = Body(*args, **kw)
        self.bodies.append(b)
        return b

    def add_circle(self, *args, **kw):
        c = StaticCircle(*args, **kw)
        self.circles.append(c)
        return c

    def add_segment(self, *args, **kw):
        s = Segment(*args, **kw)
        self.segments.append(s)
        return s

    def _scan(self, i, b, trem, ignored):
        """返回运动体 b 在剩余时间 trem 内的最早事件。"""
        best = None

        def keep(cand, key, other, nx=0.0, ny=0.0):
            nonlocal best
            if cand is None or key in ignored:
                return
            t = cand[0]
            kind = cand[1]
            if best is None or t < best[0]:
                best = (t, kind, other, nx, ny, key)

        for j, c in enumerate(self.circles):
            res = sweep_circle_circle(b.x, b.y, b.vx, b.vy, b.r,
                                      c.x, c.y, c.r, trem)
            if res is not None:
                t, kind = res
                hx = b.x + b.vx * t - c.x
                hy = b.y + b.vy * t - c.y
                d = math.hypot(hx, hy)
                nx, ny = (hx / d, hy / d) if d > 0 else (0.0, 0.0)
                keep(res, (i, "c", j), c.name, nx, ny)

        for j, s in enumerate(self.segments):
            res = sweep_circle_segment(b.x, b.y, b.vx, b.vy, b.r,
                                       s.ax, s.ay, s.bx, s.by, trem)
            if res is not None:
                keep(res, (i, "s", j), s.name, res[2], res[3])

        for k in range(i + 1, len(self.bodies)):
            o = self.bodies[k]
            res = sweep_circle_circle(
                b.x, b.y, b.vx - o.vx, b.vy - o.vy, b.r,
                o.x, o.y, o.r, trem)
            if res is not None:
                t, kind = res
                hx = b.x + b.vx * t - (o.x + o.vx * t)
                hy = b.y + b.vy * t - (o.y + o.vy * t)
                d = math.hypot(hx, hy)
                nx, ny = (hx / d, hy / d) if d > 0 else (0.0, 0.0)
                keep(res, (i, "b", k), o.name, nx, ny)
        return best

    def step(self, dt, max_events=1000):
        events = []
        ignored = set()
        t_now = 0.0
        while t_now < dt and len(events) < max_events:
            best = None
            for i, b in enumerate(self.bodies):
                cand = self._scan(i, b, dt - t_now, ignored)
                if cand is not None and (best is None or cand[0] < best[0]):
                    best = (cand[0], cand[1], i, cand[2], cand[3], cand[4], cand[5])
            if best is None:
                break
            t, kind, i, other, nx, ny, key = best
            for b in self.bodies:
                b.x += b.vx * t
                b.y += b.vy * t
            t_now += t
            b = self.bodies[i]
            if kind == HIT:
                events.append(Event(t_now, "collision", b.name, other, nx, ny))
                if key[1] == "b":
                    o = self.bodies[key[2]]
                    rvx = b.vx - o.vx
                    rvy = b.vy - o.vy
                    j = rvx * nx + rvy * ny
                    if j < 0.0:  # 等质量弹性碰撞
                        b.vx -= j * nx
                        b.vy -= j * ny
                        o.vx += j * nx
                        o.vy += j * ny
                else:
                    vn = b.vx * nx + b.vy * ny
                    if vn < 0.0:  # 静止障碍：镜面反弹
                        b.vx -= 2.0 * vn * nx
                        b.vy -= 2.0 * vn * ny
            elif kind == TANGENT:
                events.append(Event(t_now, "tangent", b.name, other, nx, ny))
                ignored.add(key)
            else:
                events.append(Event(t_now, "overlap", b.name, other, nx, ny))
                ignored.add(key)
        rem = dt - t_now
        for b in self.bodies:
            b.x += b.vx * rem
            b.y += b.vy * rem
        return events
