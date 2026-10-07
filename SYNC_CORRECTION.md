# 字幕与音频同步校正：判定依据与交付说明

Python 3 标准库实现，无第三方依赖。源码：`sync_correct.py`（库）、`selftest.py`（自测 + 性能）。
完整运行输出（全部残差与成对误差数据）：`selftest_output.txt`。

## 模型

锚点 = 同一语音事件在字幕时间轴和音频时间轴上的时刻对 `(sub_time, audio_time)`。

```
audio_time = offset + scale * sub_time
校正: corrected(sub_time) = offset + scale * sub_time
```

- 固定偏移：scale = 1，offset ≠ 0（整条字幕统一早/晚几秒）
- 线性漂移：scale ≠ 1（误差随时间线性增长，越往后越偏）
- 两者叠加：offset ≠ 0 且 scale ≠ 1

## 判定依据（固定偏移 vs 线性漂移）

1. **锚点数量门槛**：锚点数 < 5（`MIN_ANCHORS`）直接拒绝，绝不用少量点外推整条时间轴。
2. **时间跨度门槛**：锚点跨度 ≤ 0 拒绝（无法估计斜率）。
3. **最小二乘拟合**：对 `y = a + b·x` 求闭式解，漂移总量 `Δ = |b−1| × 锚点时间跨度`。
4. **显著性判定**：
   - `Δ ≤ 0.3s`（`DRIFT_TOLERANCE_S`）→ 判为**固定偏移**，退化为常数模型
     `offset = mean(audio_time − sub_time)`、scale = 1。
     依据：漂移在整条轴上的实际影响小于 0.3s（约 7 帧），属于锚点噪声，
     不允许用噪声拟合出一条“假漂移”直线。
   - `Δ > 0.3s` → 判为**线性漂移**（若同时 offset ≠ 0 则为叠加，输出两者数值）。
5. **残差校验**：拟合后逐锚点计算残差 `rᵢ = yᵢ − (a + b·xᵢ)`。
   `max|rᵢ| > 0.5s`（`MAX_RESIDUAL_S`）→ **报告全部残差并拒绝应用校正**，
   提示存在离群锚点或非线性失真，不硬套一条直线。

## 自测情形与判定结果

| 情形 | 判定 | offset | scale | 残差 max/RMSE | 结果 |
|---|---|---|---|---|---|
| 仅固定偏移 +2.5s | fixed_offset | +2.4993s | 1.0 | 0.039s / 0.025s | 应用 |
| 仅漂移 scale=1.002 | linear_drift | +0.0078s | 1.001999 | 0.043s / 0.023s | 应用 |
| 叠加 (+1.2s, 1.0015) | linear_drift | +1.1868s | 1.001508 | 0.039s / 0.021s | 应用 |
| 锚点极少（3 个） | rejected | — | — | — | 拒绝：3 < 5 |
| 离群锚点（单点偏 2.5s） | linear_drift 但 ok=False | — | — | 2.403s / 0.552s | 拒绝应用并报告残差 |

每个可拟合情形均输出 20 个锚点的**逐点残差**和**校正前后成对误差表**，
完整数据见 `selftest_output.txt`。改善幅度（RMSE）：

- 固定偏移：2.4994s → 0.0254s，改善 **99.0%**
- 仅漂移：4.1111s → 0.0229s，改善 **99.4%**
- 叠加：4.1756s → 0.0205s，改善 **99.5%**
- 含离群点：拟合直线后仍有 2.4s 残差 → 拒绝（RMSE 0.55s 看似改善，
  但逐点残差暴露了离群点，证明不能只看 RMSE）

## 10 万条字幕性能（本机 Python 3.12）

- 仅校正 10 万条：约 **55 ms**
- 解析 SRT + 校正 + 重新序列化全流程：约 **2.8–3.3 s**
- Python 对象层内存峰值（`tracemalloc`，含输入/解析/输出）：约 **43.4 MB**

## 运行命令

```bash
python3 selftest.py                       # 自测 + 10 万条性能（输出到 stdout）
python3 selftest.py > selftest_output.txt # 留存完整残差/误差/耗时数据
```

作为库使用：

```python
from sync_correct import Anchor, fit, parse_srt, correct_cues, dump_srt

res = fit([Anchor(sub_t, audio_t), ...])
if not res.ok:
    print(res.reason)          # 锚点不足 / 残差过大
else:
    cues = parse_srt(open("in.srt", encoding="utf-8").read())
    open("out.srt", "w", encoding="utf-8").write(dump_srt(correct_cues(cues, res)))
```
