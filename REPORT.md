# 图像拼接与重叠融合 — 实验报告

纯 Python 3 标准库实现（无第三方依赖）。源码见 `seamstitch/`，
自测 `selftest.py`，用例 `run_cases.py`，基准 `benchmark.py`。

## 1. 接缝搜索方式与代价定义

重叠区视为 `W × H` 网格（W = 重叠宽度，H = 图高）。接缝是一条从顶行到
底行的 8 连通竖直路径：每行选一个列，相邻两行列号差 ≤ 1。

**代价定义**（`seamstitch/stitch.py`）：

```
cost(y, x) = mean|RGB_L(x) − RGB_R(x)|        # 颜色差异能量
           + grad_weight × (|∇L| + |∇R|)      # 边缘回避能量，grad_weight = 4
```

- 第一项：两图在该像素的颜色差，两图一致处切缝不可见；
- 第二项：亮度梯度幅值（中心差分）。高梯度 = 高对比内容（主体边缘），
  施加大惩罚，接缝即绕开主体。

**搜索算法**：动态规划求最小累计代价路径

```
M(0, x) = cost(0, x)
M(y, x) = cost(y, x) + min{ M(y−1, x−1), M(y−1, x), M(y−1, x+1) }
```

从 `argmin M(H−1, ·)` 回溯得到路径。复杂度 O(W·H)。

**主体穿越统计**：高对比遮罩 = 梯度 > 48 的像素（再膨胀 1 像素，贴边也算
穿越）。统计路径落在遮罩上的像素数 `crossings_pixels` 与连续段数
`crossings_segments`。

## 2. 曝光补偿

拼接前在重叠区分别求左右图平均亮度，增益 `g = mean_R / mean_L`
（钳制到 [0.5, 2]）乘到左图全部像素，再做接缝搜索与融合。

## 3. 重叠区过小的确定退路

`overlap < min_overlap`（默认 8 px）时确定性分支，由 `on_too_small` 决定：

- `"blend"`：退化为整段重叠区线性渐变融合（`fallback_blend`）；
- `"reject"`：直接拒绝，返回 `status="rejected"`、无图像。

## 4. 用例结果（`python3 run_cases.py`）

| 用例 | 重叠(px) | 状态 | 穿主体(像素/段) | 亮度差 前→后 | 增益 |
|---|---|---|---|---|---|
| normal（主体在重叠区） | 90 | seam | 0 / 0 | 0.00 → 0.00 | 1.0000 |
| large_overlap（73% 重叠） | 240 | seam | 0 / 0 | 0.00 → 0.00 | 1.0000 |
| small_overlap_blend（4px） | 4 | fallback_blend | – | 0.00 → 0.00 | – |
| small_overlap_reject（4px） | 4 | rejected | – | – | – |
| exposure_difference（右图 1.6×） | 80 | seam | 0 / 0 | 75.15 → 6.25 | 1.5145 |
| forced_subject（条纹柱横贯整个重叠区） | 100 | seam | 56 / 14 | 0.00 → 0.00 | 1.0000 |

- 接缝可绕开时穿越为 0（normal / large_overlap / exposure_difference）；
- 必经主体时穿越 56 像素 / 14 段，且走的是全图最小代价路径
  （总代价 26544.0，DP 保证最优）；两图内容一致，融合后无可见割裂；
- 曝光差异：补偿前重叠区平均亮度差 75.15（左 146.06 / 右 221.21），
  增益 1.5145 补偿后降至 6.25（残余为噪声与量化误差）。

## 5. 接缝路径数据

完整路径（每行的接缝列号，重叠区局部坐标）见 `out/seams.json`。摘录：

```
normal:               [14, 13, 13, 14, 15, 14, 13, 14, 13, 14, ...]  cost=830.43
large_overlap:        [39, 39, 39, 39, 40, 40, 40, 40, 40, 39, ...]  cost=430.65
exposure_difference:  [27, 27, 27, 27, 27, 27, 27, 27, 27, 27, ...]  cost=1014.66
forced_subject:       [0, 0, 0, ...]  （整个重叠区皆主体，贴左缘代价最小） cost=26544.0
small_overlap_*:      无路径（退路分支）
```

结果图：`out/{normal,large_overlap,exposure_difference,forced_subject,small_overlap_blend}.png/.ppm`。

## 6. 耗时（1000 组，`python3 benchmark.py`）

160×64 视图、重叠 12–120 px、随机主体/曝光，仅计时 `stitch()` 本身。
机器：x86-64，Python 3.12.3，纯解释执行。

| 指标 | 数值 |
|---|---|
| 总耗时 | 6.5852 s |
| 单组均值 | 6.5834 ms |
| 标准差 | 8.3459 ms |
| min / p50 / p90 / p95 / p99 / max (ms) | 1.38 / 6.00 / 10.75 / 11.45 / 13.20 / 183.17 |

单组耗时分布（ms）：

| 区间 | [1,2) | [2,3) | [3,5) | [5,8) | [8,12) | [12,20) | [20,∞) |
|---|---|---|---|---|---|---|---|
| 组数 | 51 | 110 | 200 | 385 | 226 | 26 | 2 |

1000 组全部走 `seam` 主路径；穿主体像素合计 79（平均每组 0.079 px），
即接缝在 1000 组随机场景中几乎全部绕开高对比内容。
明细见 `out/benchmark.json`。

## 7. 运行命令

```bash
python3 selftest.py      # 7 项自测（重建误差、避主体、必经主体、曝光、退路、大重叠、PPM 往返）
python3 run_cases.py     # 6 个用例 -> out/*.png|ppm, out/seams.json, out/brightness.json
python3 benchmark.py     # 1000 组基准 -> out/benchmark.json（可传参数改组数）
```

库用法：

```python
from seamstitch import stitch
res = stitch(left_img, right_img, offset_x=90,
             min_overlap=8, on_too_small="blend")  # 或 "reject"
res.status            # "seam" | "fallback_blend" | "rejected"
res.seam              # 每行接缝列号（重叠区局部坐标）
res.crossings_pixels  # 穿主体像素数
res.lum_diff_before, res.lum_diff_after  # 曝光补偿前后亮度差
res.image.save_ppm("out.ppm")
```
