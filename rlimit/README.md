# rlimit — 资源限制的继承与覆盖（Python 3，仅标准库）

核心原则：子级可以把限制调小，但不能调得比父级还大，否则隔离形同虚设。

## 继承与覆盖规则

| 编号 | 规则 |
|------|------|
| R1 | 所有维度均为 **min-wins**：数值越小越严格，生效值 = 链上所有显式值的最小值 |
| R2 | **缺失 ≠ 零值**：缺失（key 不存在或值为 `None`）表示本层不表态，原样继承父级生效值；显式 `0` 表示"配额为 0 / 禁止使用"，是最严值 |
| R3 | 子级只允许 `raw <= 父级生效值`（调小/持平），不允许调大 |
| R4 | 越限处理两种模式：`clamp`（默认，收敛到父级上限并记录 `Violation`）；`reject`（抛 `LimitExceeded`） |
| R5 | 根节点某维度缺失时，默认不限制（`+inf`） |
| R6 | 零值沿链向下锁定：下级任何 `> 0` 的设置都视为越限，收敛到 0 |

## 规则表（按维度）

| 维度 | 方向 | 缺失(None/未设置) | 零值(显式 0) | 子级请求 > 父级生效上限 |
|------|------|-------------------|--------------|--------------------------|
| cpu_cores | min 胜 | 继承父级生效值 | 显式禁止，沿链锁定 | clamp: 收敛到父级上限并记录 |
| memory_mb | min 胜 | 继承父级生效值 | 显式禁止，沿链锁定 | reject: 抛 LimitExceeded |
| pids | min 胜 | 继承父级生效值 | 显式禁止，沿链锁定 | （两种模式可选，默认 clamp） |
| disk_mb | min 胜 | 继承父级生效值 | 显式禁止，沿链锁定 | 根级缺失 -> 默认不限(inf) |
| net_mbps | min 胜 | 继承父级生效值 | 显式禁止，沿链锁定 | |

## 来源链

每个生效值携带完整来源链 `ChainEntry(path, dim, raw, action, value)`：
- `resolve_tree()` 返回的链只记录"有动作"的层（set/zero/clamp/root-default），继承层**共享父级链对象**（不可变 tuple，零额外内存）；
- `explain(root, path, dim)` 重建**逐层**完整链（含缺失继承层），用于人工审计。

示例（`root/a/a1/a1x` 的 cpu_cores，root=4 → a=2 → a1=8 越限 → a1x=1）：

```
层 root             raw=4    动作=set          过层后生效值=4
层 root/a           raw=2    动作=set          过层后生效值=2
层 root/a/a1        raw=8    动作=clamp(8>2)   过层后生效值=2
层 root/a/a1/a1x    raw=1    动作=set          过层后生效值=1
```

## 越限处理结果（demo 树，clamp 模式）

```
root/a/a1    cpu_cores: 请求 8  > 父级上限 2 -> clamped-to-parent
root/a/a1/a1x pids:     请求 32 > 父级上限 0 -> clamped-to-parent
root/b/b1    cpu_cores: 请求 2  > 父级上限 0 -> clamped-to-parent
```

`reject` 模式下首个越限即抛 `LimitExceeded`（见测试 `test_reject_mode_raises`）。

## 缺失 vs 零值判定

同父（cpu=4）下：缺失子节点生效值 = 4（继承父级）；零值子节点生效值 = 0（显式禁止）。二者结果不同，见测试 `test_missing_vs_zero_differ`。

## 性能（10 万节点、5 层嵌套）

环境：Python 3.12.3，单次 `resolve_tree()` 全量解析（含来源链构建）：

| 指标 | 数值 |
|------|------|
| 节点总数 | 100,001（5 层：10/90/900/9,000/90,000 + root） |
| 解析耗时 | ~3.2 s（约 32 µs/节点） |
| 内存峰值（tracemalloc） | ~114 MiB |
| 越限收敛次数 | 177,723 |

## 运行命令

```bash
cd rlimit
python3 -m unittest -v test_rlimit   # 自测（11 个用例）
python3 demo.py                      # 规则表 + 来源链 + 越限处理 + 缺失vs零值
python3 bench.py                     # 10 万节点 5 层基准
```

## 文件

- `rlimit.py` — 库（`Node` / `resolve_tree` / `explain` / `ChainEntry` / `Violation` / `LimitExceeded`）
- `test_rlimit.py` — 自测：单层覆盖、多层嵌套、子级更大（clamp/reject）、零值、缺失、None 等价缺失、逐层来源链
- `demo.py` — 规则演示与来源链打印
- `bench.py` — 10 万节点 5 层基准
