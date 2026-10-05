# 挂载卷引用与回收：定义说明

## 角色与数据结构

- **卷（Volume）**：可被回收的存储单元，唯一 id，注册于 `VolumeGC._volumes`。
- **引用者（Container）**：挂载卷的容器，唯一 id；存活集合为
  `VolumeGC._live_containers`，`destroy_container` 将其移出。
- **引用（Ref）**：一条挂载记录，四元组
  `(container_id, volume_id, mount_path, temporary)`，整体唯一、重复挂载幂等。

引用关系的**建立**：`mount(container, volume, path, temporary=False)`，
要求容器存活且卷存在，否则抛 `KeyError`（拒绝产生无效边）。
引用关系的**记录方式**：双向索引——

- 正向：`_refs_by_volume[volume_id][ref.key] = Ref`（查某卷的引用者）；
- 反向：`_refs_by_container[container_id] -> set(ref.key)`（容器销毁时定位边）。

## “仍被引用”的定义

一条边有三种互斥状态：

| 状态 | 条件 |
| --- | --- |
| `live`（有效） | 引用者在存活集合中，且 `temporary=False` |
| `dangling`（悬空） | 引用者不在存活集合中（容器已销毁） |
| `temporary`（临时） | `temporary=True`（且引用者仍存活） |

> **卷 V 仍被引用 ⇔ 存在至少一条 `live` 边指向 V。**
> 即 `VolumeGC.live_refs(v)` 非空，`referrers(v)` 为其引用者列表。

悬空边与临时边**不构成保护**。判定的唯一依据是“当前存活集合”：
同 id 容器销毁后重新注册，视为同一逻辑容器回归，未被清理的边恢复有效；
已被 `prune_dangling` 删除的边不会复活，必须重新 `mount`。

## 悬空引用与临时挂载的确定处理

`reclaim()` 按固定顺序执行，结果确定（所有输出按 id 排序）：

1. `prune_dangling()`：删除全部悬空边，逐条记录返回；
2. `sweep_temporary()`：摘除全部临时挂载边，逐条记录返回；
3. 对剩余无 `live` 边的卷执行删除，其余保留；
4. `validate(reclaimed, kept)` 校验两条不变式：
   - 已回收卷：注册表中不存在、无残留边、无有效引用者；
   - 保留卷：注册表中存在、且 `live_referrer_count >= 1`。

## 引用图数据

- `reference_graph()`：`{volume_id: [有效引用者...]}`（排序、去重），
  示例见 `out/reference_graph.json`；
- `full_edge_list()`：全部已记录边及状态标注，见 `out/edges.json`。

## 运行命令

```bash
python3 test_volume_gc.py -v   # 15 个自测（无引用/单引用/多引用/悬空/临时/校验）
python3 demo.py                # 场景演示，产出 out/*.json
python3 benchmark.py           # 10 万卷 / 50 万引用压测，产出 out/benchmark.json
```

## 性能（本机实测，Python 3.12.3，Linux x86_64，seed=42）

10 万卷、6 万容器、50 万条引用（含 2.5 万临时边、6 千容器销毁）：

| 阶段 | 耗时 |
| --- | --- |
| 建立拓扑（10 万卷 + 6 万容器） | 0.34 s |
| 记录 50 万条引用 | 8.66 s（tracemalloc 开启，有额外开销） |
| 判定（生成引用图 + 找出可回收卷） | 1.79 s |
| 回收（清悬空/临时边 + 删卷） | 1.60 s |
| 校验 | 1.32 s |

内存峰值：Python 堆（tracemalloc）316 MiB；进程 RSS 峰值 844 MiB。
判定结果：98,612 卷被引用、1,388 卷可回收；清悬空边 50,063 条、
临时边 22,560 条；校验通过。详见 `out/benchmark.json`。
