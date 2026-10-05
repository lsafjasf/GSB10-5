"""高度图插值与法线计算库（仅使用 Python 3 标准库）。

约定
----
- 高度图按行主序（row-major）存于一维 ``array('f')``（float32），
  缺失高度用 ``float('nan')`` 表示。
- 坐标系：x 向右（列方向），y 向下（行方向），z 向上；
  地形法线必须指向 +z 半球（n_z > 0）。
- 网格在 x、y 方向等间距，间距记为 ``spacing``。

处理顺序（重要）
----------------
先对高度做双线性插值放大，再在放大后的网格上用中心差分求法线，
而不是“先在低分辨率网格上算法线，再插值法线”。原因：

1. 法线由高度的一阶导数决定，插值高度与求导必须在同一套离散
   曲面上完成，结果才与实际渲染/使用的曲面一致。
2. 直接插值法线向量会跨越悬崖两侧，把“朝上”和“朝侧面”两个
   完全不同的法线做平均，得到两者之间一个并不存在的中间法线；
   且线性插值后的向量不再是单位向量。
3. 先插值高度再求导，悬崖的位置保持不变，斜变只发生在悬崖所在
   的那一个粗网格单元内；低分辨率网格无法恢复的高频细节（悬崖
   两侧被采样成一个台阶）无法靠插值凭空恢复，因此悬崖附近相对
   高分辨率参考法线的误差始终最大，并随分辨率提高而收敛。
"""

import math
from array import array


class HeightmapError(Exception):
    """高度图数据无法按确定规则处理时抛出。"""


def fill_missing(width, height, heightmap):
    """按确定规则填补缺失（NaN）高度。

    规则（完全确定，结果与扫描顺序绑定且唯一）：

      1. 整张图全部缺失 -> 抛出 :class:`HeightmapError`（拒绝）。
      2. 否则按固定的行主序反复扫描缺失格点：取该点在扫描时刻
         已有效的 4 邻域（上、下、左、右）高度的算术平均作为
         填补值；本轮新填入的值可被同轮后续格点使用
         （Gauss-Seidel 式传播）。
      3. 重复扫描直到没有缺失。若某一轮扫描没有任何格点被填补，
         说明剩余缺失区域与任何有效格点都不连通 -> 拒绝。

    等价描述：每个缺失点最终取的是“到有效区域距离最小”的一层
    邻域均值，距有效区域越远的点参与平均的轮次越多（一种确定性
    的邻域扩散填补）。

    :return: ``(filled_grid, filled_count)``，filled_grid 为新
             分配的 ``array('f')``，原数组不被修改。
    """
    if width <= 0 or height <= 0:
        raise HeightmapError("宽高必须为正数")
    grid = array('f', heightmap)
    missing = [i for i, v in enumerate(grid) if math.isnan(v)]
    if not missing:
        return grid, 0
    if len(missing) == width * height:
        raise HeightmapError("全部高度缺失，拒绝处理")

    total = width * height
    filled_count = 0
    remaining = missing
    while remaining:
        next_remaining = []
        progress = False
        for i in remaining:
            x = i % width
            y = i // width
            acc = 0.0
            cnt = 0
            if x > 0:
                v = grid[i - 1]
                if not math.isnan(v):
                    acc += v
                    cnt += 1
            if x + 1 < width:
                v = grid[i + 1]
                if not math.isnan(v):
                    acc += v
                    cnt += 1
            if y > 0:
                v = grid[i - width]
                if not math.isnan(v):
                    acc += v
                    cnt += 1
            if y + 1 < height:
                v = grid[i + width]
                if not math.isnan(v):
                    acc += v
                    cnt += 1
            if cnt:
                grid[i] = acc / cnt
                filled_count += 1
                progress = True
            else:
                next_remaining.append(i)
        if not progress:
            raise HeightmapError(
                "%d 个缺失格点与有效区域不连通，拒绝处理" % len(next_remaining))
        remaining = next_remaining

    assert total - len(missing) + filled_count == total
    return grid, filled_count


def downsample(width, height, grid, factor):
    """整数倍抽取（取每个 factor x factor 块的左上角样本）。"""
    if factor < 1:
        raise HeightmapError("factor 必须 >= 1")
    out_w = (width - 1) // factor + 1
    out_h = (height - 1) // factor + 1
    out = array('f', bytes(4 * out_w * out_h))
    for oy in range(out_h):
        src_row = oy * factor * width
        dst_row = oy * out_w
        for ox in range(out_w):
            out[dst_row + ox] = grid[src_row + ox * factor]
    return out, out_w, out_h


def upsample_bilinear(width, height, grid, factor):
    """双线性插值，整数倍放大。

    输出尺寸为 ``((width-1)*factor+1, (height-1)*factor+1)``，
    即粗网格角点与放大结果严格重合（网格闭合，无外推）。
    """
    if factor < 1:
        raise HeightmapError("factor 必须 >= 1")
    if width < 2 or height < 2:
        raise HeightmapError("双线性插值要求宽高均 >= 2")
    if factor == 1:
        return array('f', grid), width, height

    out_w = (width - 1) * factor + 1
    out_h = (height - 1) * factor + 1
    out = array('f', bytes(4 * out_w * out_h))
    inv_f = 1.0 / factor

    for oy in range(out_h):
        gy = oy * inv_f
        y0 = int(gy)
        fy = gy - y0
        if y0 > height - 2:
            y0 = height - 2
            fy = 1.0
        row0 = y0 * width
        row1 = row0 + width
        out_row = oy * out_w
        for ox in range(out_w):
            gx = ox * inv_f
            x0 = int(gx)
            fx = gx - x0
            if x0 > width - 2:
                x0 = width - 2
                fx = 1.0
            i00 = row0 + x0
            i10 = i00 + 1
            i01 = row1 + x0
            i11 = i01 + 1
            v00 = grid[i00]
            v10 = grid[i10]
            v01 = grid[i01]
            v11 = grid[i11]
            top = v00 + (v10 - v00) * fx
            bottom = v01 + (v11 - v01) * fx
            out[out_row + ox] = top + (bottom - top) * fy
    return out, out_w, out_h


def compute_normals(width, height, grid, spacing):
    """由高度场求逐点单位法线。

    方法：x/y 方向中心差分
    ``dz/dx = (z[x+1]-z[x-1]) / (2*spacing)``，
    边界退化为单侧差分。曲面 ``z = f(x,y)`` 的法线取

        n = normalize((-dz/dx, -dz/dy, 1))

    第三分量恒为 +1，故归一化后必有 n_z > 0，法线确定指向
    地形上方；输出为长度 ``3*width*height`` 的 ``array('f')``。
    """
    if spacing <= 0.0:
        raise HeightmapError("spacing 必须为正数")
    count = width * height
    out = array('f', bytes(12 * count))
    half_inv = 1.0 / (2.0 * spacing)

    for y in range(height):
        row = y * width
        up = row - width if y > 0 else row
        down = row + width if y < height - 1 else row
        for x in range(width):
            i = row + x
            left = i - 1 if x > 0 else i
            right = i + 1 if x < width - 1 else i
            if x == 0:
                dzdx = (grid[right] - grid[i]) / spacing
            elif x + 1 == width:
                dzdx = (grid[i] - grid[left]) / spacing
            else:
                dzdx = (grid[right] - grid[left]) * half_inv
            if y == 0:
                dzdy = (grid[down + x] - grid[i]) / spacing
            elif y + 1 == height:
                dzdy = (grid[i] - grid[up + x]) / spacing
            else:
                dzdy = (grid[down + x] - grid[up + x]) * half_inv

            nx = -dzdx
            ny = -dzdy
            inv_len = 1.0 / math.sqrt(nx * nx + ny * ny + 1.0)
            j = 3 * i
            out[j] = nx * inv_len
            out[j + 1] = ny * inv_len
            out[j + 2] = inv_len
    return out


def interpolate_normals(width, height, normals, factor):
    """对照组：先算法线再分别双线性插值法线三个分量，再归一化。"""
    count = width * height
    comps = []
    for c in range(3):
        comp = array('f', bytes(4 * count))
        for i in range(count):
            comp[i] = normals[3 * i + c]
        up, out_w, out_h = upsample_bilinear(width, height, comp, factor)
        comps.append(up)
    out_count = out_w * out_h
    out = array('f', bytes(12 * out_count))
    for i in range(out_count):
        nx = comps[0][i]
        ny = comps[1][i]
        nz = comps[2][i]
        inv_len = 1.0 / math.sqrt(nx * nx + ny * ny + nz * nz)
        out[3 * i] = nx * inv_len
        out[3 * i + 1] = ny * inv_len
        out[3 * i + 2] = nz * inv_len
    return out, out_w, out_h


def validate_normals(normals):
    """逐点校验法线：返回 (max |‖n‖-1|, min n_z)。

    调用方应断言 max 偏差很小且 min n_z > 0（归一 + 朝上）。
    """
    count = len(normals) // 3
    max_dev = 0.0
    min_nz = float("inf")
    for i in range(count):
        j = 3 * i
        nx = normals[j]
        ny = normals[j + 1]
        nz = normals[j + 2]
        length = math.sqrt(nx * nx + ny * ny + nz * nz)
        dev = abs(length - 1.0)
        if dev > max_dev:
            max_dev = dev
        if nz < min_nz:
            min_nz = nz
    return max_dev, min_nz


def angle_errors(reference, candidate):
    """逐点法线夹角（度），返回 (平均, 最大) 以及逐点结果列表。"""
    count = len(reference) // 3
    if len(candidate) != len(reference):
        raise HeightmapError("法线数量不一致，无法逐点对比")
    per_point = array('f', bytes(4 * count))
    total = 0.0
    worst = 0.0
    worst_i = 0
    for i in range(count):
        j = 3 * i
        dot = (reference[j] * candidate[j]
               + reference[j + 1] * candidate[j + 1]
               + reference[j + 2] * candidate[j + 2])
        if dot > 1.0:
            dot = 1.0
        elif dot < -1.0:
            dot = -1.0
        ang = math.degrees(math.acos(dot))
        per_point[i] = ang
        total += ang
        if ang > worst:
            worst = ang
            worst_i = i
    return total / count, worst, worst_i, per_point
