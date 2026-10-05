"""
volume_gc.py — 挂载卷的引用追踪与安全回收库（仅标准库）。

核心模型
--------
- Volume  : 可被回收的挂载卷，有唯一 id。
- Container: 引用者（容器），有唯一 id；容器可被销毁。
- Ref     : 一条引用记录 (container_id, volume_id, mount_path, temporary)。

判定规则见 DEFINITIONS.md，核心一句话：
卷 V “仍被引用” 当且仅当存在一条 Ref，其引用者容器仍然存在，且该引用不是临时挂载。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple


@dataclass(frozen=True)
class Ref:
    """一条挂载引用，四元组整体唯一。"""

    container_id: str
    volume_id: str
    mount_path: str
    temporary: bool = False

    @property
    def key(self) -> Tuple[str, str, str, bool]:
        return (self.container_id, self.volume_id, self.mount_path, self.temporary)


@dataclass
class ReclaimResult:
    """一次回收的确定性结果。"""

    reclaimed: List[str] = field(default_factory=list)
    kept: List[str] = field(default_factory=list)
    pruned_dangling: List[Ref] = field(default_factory=list)
    pruned_temporary: List[Ref] = field(default_factory=list)
    unknown_volumes: List[str] = field(default_factory=list)


class VolumeGC:
    """卷引用注册表与回收器。

    内部数据结构：
    - _volumes:           volume_id -> {} 存在即卷活着
    - _live_containers:   仍然存在的容器 id 集合
    - _refs_by_volume:    volume_id -> {ref.key: Ref}（引用图正向边）
    - _refs_by_container: container_id -> set(ref.key)（用于容器销毁时定位边）
    """

    def __init__(self) -> None:
        self._volumes: Dict[str, Dict[str, int]] = {}
        self._live_containers: Set[str] = set()
        self._refs_by_volume: Dict[str, Dict[Tuple[str, str, str, bool], Ref]] = {}
        self._refs_by_container: Dict[str, Set[Tuple[str, str, str, bool]]] = {}

    # ---- 拓扑变更 -------------------------------------------------------

    def add_volume(self, volume_id: str) -> None:
        self._volumes[volume_id] = {}
        self._refs_by_volume.setdefault(volume_id, {})

    def has_volume(self, volume_id: str) -> bool:
        return volume_id in self._volumes

    def register_container(self, container_id: str) -> None:
        self._live_containers.add(container_id)

    def destroy_container(self, container_id: str) -> None:
        """销毁容器：容器立即从存活集合移除。

        其留下的边成为悬空引用，直到下一次 sweep/prune 被确定性清除；
        在清除之前这些边也不计入“存活引用”（见 live_refs）。
        """
        self._live_containers.discard(container_id)

    def container_exists(self, container_id: str) -> bool:
        return container_id in self._live_containers

    # ---- 引用关系的建立与记录 -------------------------------------------

    def mount(
        self,
        container_id: str,
        volume_id: str,
        mount_path: str,
        temporary: bool = False,
    ) -> Ref:
        """建立一条引用（容器挂载卷）。

        - 挂载时容器必须存在，卷必须存在，否则 KeyError（拒绝产生无效边）。
        - 重复挂载（四元组完全相同）幂等返回。
        """
        if container_id not in self._live_containers:
            raise KeyError(f"container not found or already destroyed: {container_id!r}")
        if volume_id not in self._volumes:
            raise KeyError(f"volume not found: {volume_id!r}")
        ref = Ref(container_id, volume_id, mount_path, temporary)
        self._refs_by_volume.setdefault(volume_id, {})[ref.key] = ref
        self._refs_by_container.setdefault(container_id, set()).add(ref.key)
        return ref

    def unmount(self, ref: Ref) -> bool:
        """显式解除一条引用；返回是否实际删除。"""
        bucket = self._refs_by_volume.get(ref.volume_id)
        existed = bool(bucket and bucket.pop(ref.key, None))
        cbucket = self._refs_by_container.get(ref.container_id)
        if cbucket is not None:
            cbucket.discard(ref.key)
        return existed

    # ---- 引用图查询 -----------------------------------------------------

    def all_refs(self, volume_id: str) -> List[Ref]:
        """该卷的全部已记录边（含悬空边与临时边，按 key 排序，确定性输出）。"""
        return [self._refs_by_volume[volume_id][k]
                for k in sorted(self._refs_by_volume.get(volume_id, {}))]

    def dangling_refs(self, volume_id: Optional[str] = None) -> List[Ref]:
        """悬空边：引用者容器已不存在的边。"""
        volumes = [volume_id] if volume_id is not None else sorted(self._refs_by_volume)
        out: List[Ref] = []
        for vid in volumes:
            for ref in self.all_refs(vid):
                if ref.container_id not in self._live_containers:
                    out.append(ref)
        return out

    def temporary_refs(self, volume_id: Optional[str] = None) -> List[Ref]:
        """临时挂载边。"""
        volumes = [volume_id] if volume_id is not None else sorted(self._refs_by_volume)
        out: List[Ref] = []
        for vid in volumes:
            for ref in self.all_refs(vid):
                if ref.temporary:
                    out.append(ref)
        return out

    def live_refs(self, volume_id: str) -> List[Ref]:
        """有效引用：引用者存在 且 非临时挂载。

        这就是“仍被引用”的判定依据：live_refs(v) 非空 ⇔ v 仍被引用。
        """
        return [
            ref
            for ref in self.all_refs(volume_id)
            if not ref.temporary and ref.container_id in self._live_containers
        ]

    def referrers(self, volume_id: str) -> List[str]:
        """该卷当前的有效引用者（去重、排序）。"""
        return sorted({r.container_id for r in self.live_refs(volume_id)})

    def is_referenced(self, volume_id: str) -> bool:
        return bool(self.live_refs(volume_id))

    def reference_graph(self) -> Dict[str, List[str]]:
        """引用图数据：volume_id -> 有效引用者 container_id 列表（排序、去重）。

        悬空边与临时边已按定义剔除；完整边（含这两类）见 full_edge_list。
        """
        return {vid: self.referrers(vid) for vid in sorted(self._volumes)}

    def full_edge_list(self) -> List[Dict[str, object]]:
        """全部已记录边的可序列化形式，标注每条边的状态。"""
        edges: List[Dict[str, object]] = []
        for vid in sorted(self._refs_by_volume):
            for ref in self.all_refs(vid):
                if ref.container_id not in self._live_containers:
                    status = "dangling"
                elif ref.temporary:
                    status = "temporary"
                else:
                    status = "live"
                edges.append({
                    "volume": vid,
                    "container": ref.container_id,
                    "mount_path": ref.mount_path,
                    "temporary": ref.temporary,
                    "status": status,
                })
        return edges

    # ---- 清理与回收 -----------------------------------------------------

    def prune_dangling(self) -> List[Ref]:
        """确定性处理悬空引用：全部删除（它们绝不可能保护卷）。

        注意：未 prune 前，若同 id 容器重新注册（回归同一逻辑容器），
        按“当前存活集合”判定，边会恢复有效；一旦被 prune 删除，
        旧边不自动恢复，需要重新 mount。
        """
        pruned: List[Ref] = []
        for vid in list(self._refs_by_volume):
            for ref in list(self.all_refs(vid)):
                if ref.container_id not in self._live_containers:
                    self.unmount(ref)
                    pruned.append(ref)
        return sorted(pruned, key=lambda r: r.key)

    def sweep_temporary(self) -> List[Ref]:
        """确定性处理临时挂载：回收阶段一律摘除，不阻止卷被回收。

        语义：temporary=True 表示一次性挂载（如 docker --tmpfs 式临时用途），
        它在业务上不构成对卷数据的长期依赖，GC 时按序清除。
        """
        swept: List[Ref] = []
        for vid in list(self._refs_by_volume):
            for ref in list(self.all_refs(vid)):
                if ref.temporary:
                    self.unmount(ref)
                    swept.append(ref)
        return sorted(swept, key=lambda r: r.key)

    def reclaimable_volumes(self) -> List[str]:
        """按当前状态计算可回收卷（无任何有效引用），id 升序，结果确定。"""
        return sorted(
            vid for vid in self._volumes
            if not self.live_refs(vid)
        )

    def reclaim(self, only: Optional[Iterable[str]] = None) -> ReclaimResult:
        """执行一次完整回收：

        1. prune_dangling  删除悬空边；
        2. sweep_temporary 摘除临时挂载边；
        3. 对无有效引用的卷执行删除；
        4. 其余卷保留。

        only 非空时只考虑给定候选卷（其余一律保留），便于增量回收。
        """
        result = ReclaimResult()
        result.pruned_dangling = self.prune_dangling()
        result.pruned_temporary = self.sweep_temporary()

        candidates = set(self._volumes) if only is None else set(only)
        result.unknown_volumes = sorted(v for v in candidates if v not in self._volumes)

        for vid in sorted(candidates):
            if vid not in self._volumes:
                continue
            if self.live_refs(vid):
                result.kept.append(vid)
            else:
                del self._volumes[vid]
                self._refs_by_volume.pop(vid, None)
                result.reclaimed.append(vid)
        return result

    # ---- 校验 -----------------------------------------------------------

    def validate(self, reclaimed: Iterable[str], kept: Iterable[str]) -> Dict[str, object]:
        """回收后校验，返回可序列化的校验数据。

        断言两条不变式：
        - 已回收卷：注册表中不存在，且不存在任何残留边；
        - 保留卷：注册表中存在，且至少有一个有效引用者。
        """
        reclaimed = sorted(reclaimed)
        kept = sorted(kept)
        checks: List[Dict[str, object]] = []
        ok = True

        for vid in reclaimed:
            residual_edges = list(self._refs_by_volume.get(vid, {}).values())
            live = self.live_refs(vid) if vid in self._volumes else []
            passed = vid not in self._volumes and not residual_edges and not live
            ok = ok and passed
            checks.append({
                "volume": vid, "expectation": "reclaimed",
                "exists": vid in self._volumes,
                "residual_edge_count": len(residual_edges),
                "live_referrer_count": len({r.container_id for r in live}),
                "passed": passed,
            })

        for vid in kept:
            referrers = self.referrers(vid)
            passed = vid in self._volumes and len(referrers) >= 1
            ok = ok and passed
            checks.append({
                "volume": vid, "expectation": "kept",
                "exists": vid in self._volumes,
                "residual_edge_count": len(self.all_refs(vid)),
                "live_referrer_count": len(referrers),
                "live_referrers": referrers,
                "passed": passed,
            })

        return {
            "passed": ok,
            "reclaimed_count": len(reclaimed),
            "kept_count": len(kept),
            "checks": checks,
        }
