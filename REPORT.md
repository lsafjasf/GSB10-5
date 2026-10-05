# 最短路径预处理加速：实验报告

纯 Python 3 标准库实现（`array` / `heapq` / `tracemalloc` / `resource`）。
方法：**ALT**（A* + Landmarks + Triangle inequality）——预处理选 L 个地标，
对每个地标存全图精确距离表；查询时用一致（consistent）启发式跑 A*，
结果**精确**（非近似），与朴素 Dijkstra 逐点一致。

## 运行命令

```bash
python3 selftest.py     # 自测 + 对拍（约 20 秒）
python3 benchmark.py    # 全部基准，结果写入 results/（约 2 分钟）
```

环境：Python 3.12.3，16 核，15 GB RAM。所有数据可用上面命令复现
（种子固定 `SEED=20261005`）。

## 源码结构

- `spp/graph.py` — CSR 正/反邻接图，含内存字节数自报
- `spp/dijkstra.py` — 朴素 Dijkstra（基线，目标点提前终止）
- `spp/alt.py` — ALT 预处理索引 + A* 查询 + 内存预算守卫
- `selftest.py` — 对拍与边界自测
- `benchmark.py` — 基准驱动，产出 `results/{summary.json,cost_curve.csv,crosscheck.csv}`
- `docs/COST_MODEL.md` — 代价模型推导

## 预处理 vs 单次查询代价（10 万点 / 50 万边）

图：400×250 带权网格（权≈几何距离+抖动）+ 随机远程边，共 100000 点、500000 有向边；L=16 地标。

| 项目 | 数值 |
|---|---|
| 预处理耗时 `T_pre` | **5.43 s**（= 2L=32 次全图 Dijkstra） |
| 预处理内存峰值（tracemalloc） | **37.2 MB**（其中索引表 25.6 MB = 2·16·100000·8B） |
| 进程 RSS 峰值（含图与全部查询） | 138.9 MB |
| 朴素单查询 `t_naive` | **60.85 ms**（200 次均值） |
| 预处理后单查询 `t_alt` | **14.89 ms**（2000 次均值），**加速 4.1×** |
| A* 展开点数 / 朴素展开点数 | 3.3%（50635 → 1663，20 对抽样） |

## 总代价曲线与临界点

模型：`T_naive(q) = q·0.0609`，`T_alt(q) = 5.43 + q·0.0149`（秒）。
临界点 `q* = T_pre/(t_naive − t_alt) ≈ 118.2` → **查询 ≤ 118 次不划算，≥ 119 次划算**。

```
q=1     |N                     P                        | naive=     0.06s alt=     5.45s
q=5     |        N             P                        | naive=     0.30s alt=     5.51s
q=10    |           N          P                        | naive=     0.61s alt=     5.58s
q=20    |              N       P                        | naive=     1.22s alt=     5.73s
q=50    |                   N   P                       | naive=     3.04s alt=     6.18s
q=100   |                       B                       | naive=     6.09s alt=     6.92s
q=200   |                        P N                    | naive=    12.17s alt=     8.41s
q=500   |                          P    N               | naive=    30.43s alt=    12.88s
q=1000  |                             P    N            | naive=    60.85s alt=    20.32s
q=2000  |                               P     N         | naive=   121.70s alt=    35.21s
q=5000  |                                   P      N    | naive=   304.25s alt=    79.88s
q=10000 |                                       P      N| naive=   608.51s alt=   154.33s
```

（N=朴素划算，P=预处理划算，B=持平；完整 19 个点见 `results/cost_curve.csv`）

## 覆盖的四种情形

**查询极少（q=10）**：朴素 0.61s vs 预处理总 5.58s —— 预处理亏 9 倍，不划算。

**查询极多（q=100000）**：朴素 6085.1s vs 预处理总 1494.4s —— 省 75%，摊薄后
预处理成本仅占 0.36%。

**图接近链状**（10 万点链 + 400 条随机弦，m=200398）：
预处理 1.82s，朴素 23.1ms/q → ALT 5.85ms/q（4.0×），临界点 106 次，
对拍 200 对 0 失配。

**预处理内存不足**（同一 100k/500k 图，构建前估算、不杀进程）：

| 预算 | 策略 | 结果 | 对拍 |
|---|---|---|---|
| 40 MB | reduce | L 自动 16→11，建索引 3.40s | 30 对 0 失配 |
| 20 MB | reduce | L=0，退化为朴素 Dijkstra | 30 对 0 失配 |
| 40 MB | raise | 抛 `MemoryBudgetExceeded`（need ~47.2MB > 40MB） | — |

## 对拍数据（预处理结果 vs 朴素 Dijkstra，必须逐点相等）

| 测试 | 规模 | 对数 | 失配 |
|---|---|---|---|
| 随机有向图全点对拍（selftest） | 40 图，n≤40 | 18435 对 | 0 |
| 不连通图 / 零权边 / 自环 / 自查询 | 小图 | 全部 | 0 |
| 链图（selftest） | n=2000 | 200 对 | 0 |
| grid-100k-500k（benchmark） | n=100000 | 200 对 | 0（见 `results/crosscheck.csv`） |
| chain-like-100k（benchmark） | n=100000 | 200 对 | 0 |
| 内存降级索引（L=11 与 L=0） | n=100000 | 各 30 对 | 0 |

样例（`results/crosscheck.csv` 前几行，距离为 float64 完全相等）：

```
s,t,naive_dist,alt_dist,equal
64854,10658,290.09444481121636,290.09444481121636,True
88346,37563,150.3506243846752,150.3506243846752,True
37839,66823,260.6992958588663,260.6992958588663,True
```

## 正确性依据

启发式 `h(v) = max( max_l d(l,t)−d(l,v), max_l d(v,l)−d(t,l), 0 )`。
由三角不等式 `d(l,t) ≤ d(l,v)+d(v,t)` 与 `d(v,l) ≤ d(v,t)+d(t,l)`，
两项都是 `d(v,t)` 的下界；每个地标启发式一致（consistent），max 仍一致，
故 A* 首次弹出 t 即为精确最短路。距离表用 float64 存整数值权重和，
无舍入误差，因此对拍是完全相等而非近似相等。
