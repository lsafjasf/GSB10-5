"""挂载卷引用与回收库（仅标准库）。

核心概念与判定定义见文件末尾的模块 docstring 续述及 README.md。
简述：
  - 卷(volume)被引用，当且仅当存在一条“有效引用”。
  - 有效引用 = 引用者(容器)仍然存活，且引用未过期（临时挂载 TTL 未到）。
  - 悬空引用（引用者已删除）与过期临时挂载不算引用，回收时会被确定地清理掉。
"""

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

PERSISTENT = "mount"   # 普通（持久）挂载
TEMPORARY = "tmp"      # 临时挂载，带 expires_at 过期时间


@dataclass(frozen=True)
class Reference:
    """一条引用记录：referrer_id -> volume_id。"""
    referrer_id: str
    volume_id: str
    kind: str = PERSISTENT
    created_at: float = 0.0
    expires_at: Optional[float] = None  # 仅临时挂载使用；None 表示不过期

    def is_live_at(self, now: float) -> bool:
        return self.expires_at is None or self.expires_at > now


@dataclass
class VolumeRegistry:
    """卷、容器（引用者）与引用关系的登记处。

    引用关系建立方式：
      create_volume(v)                       登记一个卷
      create_container(c)                    登记一个引用者（容器），出生即“存活”
      add_ref(c, v, ...)                     建立 c 对 v 的引用（幂等）
      remove_container(c) / delete_volume(v) 删除引用者或卷

    记录结构：
      refs_by_referrer[referrer] = {volume: Reference}
      refs_by_volume[volume]    = {referrer: Reference}
      live_containers: 仍存在的引用者集合
      volumes: 已登记且未删除的卷集合
    """
    volumes: Set[str] = field(default_factory=set)
    live_containers: Set[str] = field(default_factory=set)
    refs_by_referrer: Dict[str, Dict[str, Reference]] = field(default_factory=dict)
    refs_by_volume: Dict[str, Dict[str, Reference]] = field(default_factory=dict)
    _dead_containers: Set[str] = field(default_factory=set)

    # ---- 登记与引用建立 ----
    def create_volume(self, volume_id: str) -> None:
        self.volumes.add(volume_id)
        self.refs_by_volume.setdefault(volume_id, {})

    def create_container(self, container_id: str) -> None:
        self.live_containers.add(container_id)
        self._dead_containers.discard(container_id)
        self.refs_by_referrer.setdefault(container_id, {})

    def add_ref(self, referrer_id: str, volume_id: str,
                kind: str = PERSISTENT, created_at: float = 0.0,
                expires_at: Optional[float] = None) -> Reference:
        """建立引用关系。引用者必须存在（已登记且存活）。"""
        if referrer_id not in self.live_containers:
            raise KeyError(f"referrer not alive: {referrer_id!r}")
        if volume_id not in self.volumes:
            raise KeyError(f"volume not found: {volume_id!r}")
        if kind == TEMPORARY and expires_at is None:
            raise ValueError("temporary ref requires expires_at")
        ref = Reference(referrer_id, volume_id, kind, created_at, expires_at)
        self.refs_by_referrer.setdefault(referrer_id, {})[volume_id] = ref
        self.refs_by_volume.setdefault(volume_id, {})[referrer_id] = ref
        return ref

    def remove_ref(self, referrer_id: str, volume_id: str) -> bool:
        existed = self.refs_by_referrer.get(referrer_id, {}).pop(volume_id, None)
        self.refs_by_volume.get(volume_id, {}).pop(referrer_id, None)
        return existed is not None

    def restore_ref_record(self, ref: Reference) -> None:
        """从快照/外部存储导入一条引用记录，不做存活校验。

        引用者可能已不存在 —— 这正是悬空引用的来源之一；
        导入后由 sweep()/garbage_collect() 做确定性清理。
        """
        self.refs_by_referrer.setdefault(ref.referrer_id, {})[ref.volume_id] = ref
        self.refs_by_volume.setdefault(ref.volume_id, {})[ref.referrer_id] = ref

    def remove_container(self, container_id: str) -> List[Reference]:
        """删除引用者：其名下全部引用立即变成悬空引用。返回被悬空化的引用。"""
        refs = list(self.refs_by_referrer.pop(container_id, {}).values())
        for ref in refs:
            self.refs_by_volume.get(ref.volume_id, {}).pop(container_id, None)
        self.live_containers.discard(container_id)
        self._dead_containers.add(container_id)
        return refs

    def delete_volume(self, volume_id: str) -> None:
        for referrer_id in list(self.refs_by_volume.get(volume_id, {})):
            self.refs_by_referrer.get(referrer_id, {}).pop(volume_id, None)
        self.refs_by_volume.pop(volume_id, None)
        self.volumes.discard(volume_id)

    # ---- 查询 ----
    def dangling_references(self) -> List[Reference]:
        """悬空引用：记录仍存在（索引中残留）但引用者已不存在。

        正常流程下 remove_container 已即时摘除其引用；
        该方法用于检测“外部存储/快照恢复”等场景里残留的非法记录。
        """
        out: List[Reference] = []
        for referrer_id, m in self.refs_by_referrer.items():
            if referrer_id not in self.live_containers:
                out.extend(m.values())
        return out

    def expired_temporary_references(self, now: float) -> List[Reference]:
        out: List[Reference] = []
        for m in self.refs_by_volume.values():
            for ref in m.values():
                if ref.kind == TEMPORARY and not ref.is_live_at(now):
                    out.append(ref)
        return out

    def live_referrers(self, volume_id: str, now: float = 0.0) -> List[str]:
        """卷 v 的有效引用者：引用者存活 且 引用未过期。"""
        return sorted(
            ref.referrer_id
            for ref in self.refs_by_volume.get(volume_id, {}).values()
            if ref.referrer_id in self.live_containers and ref.is_live_at(now)
        )

    def is_referenced(self, volume_id: str, now: float = 0.0) -> bool:
        """“仍被引用”的判定定义：存在至少一个有效引用者。"""
        return bool(self.live_referrers(volume_id, now))

    def reference_graph(self) -> Dict[str, List[str]]:
        """引用图数据：volume_id -> [有效引用者...]（悬空/过期引用不计入）。"""
        return {v: self.live_referrers(v) for v in sorted(self.volumes)}

    def raw_graph(self, now: float = 0.0) -> Dict[str, Dict[str, dict]]:
        """原始图（含悬空/过期标记），供审计导出。"""
        graph: Dict[str, Dict[str, dict]] = {}
        for volume_id in sorted(self.volumes):
            nodes: Dict[str, dict] = {}
            for ref in self.refs_by_volume.get(volume_id, {}).values():
                alive = ref.referrer_id in self.live_containers
                nodes[ref.referrer_id] = {
                    "kind": ref.kind,
                    "expires_at": ref.expires_at,
                    "referrer_alive": alive,
                    "valid": alive and ref.is_live_at(now),
                }
            graph[volume_id] = nodes
        return graph

    # ---- 清理悬空/过期记录 ----
    def sweep(self, now: float) -> Tuple[List[Reference], List[Reference]]:
        """确定性清理：摘除悬空引用与过期临时挂载。返回 (悬空, 过期) 引用列表。"""
        dangling: List[Reference] = []
        for referrer_id in list(self.refs_by_referrer):
            if referrer_id not in self.live_containers:
                for ref in list(self.refs_by_referrer[referrer_id].values()):
                    dangling.append(ref)
                    self.refs_by_volume.get(ref.volume_id, {}).pop(referrer_id, None)
                del self.refs_by_referrer[referrer_id]

        expired: List[Reference] = []
        for referrer_id, m in self.refs_by_referrer.items():
            for volume_id in list(m):
                ref = m[volume_id]
                if ref.kind == TEMPORARY and not ref.is_live_at(now):
                    expired.append(ref)
                    del m[volume_id]
                    self.refs_by_volume.get(volume_id, {}).pop(referrer_id, None)
        return dangling, expired


@dataclass
class GCReport:
    reclaimed: List[str]                       # 被回收的卷（确认零有效引用）
    retained: List[str]                        # 未回收的卷（确认有有效引用）
    referrers_of: Dict[str, List[str]]         # 判定依据：每个卷的有效引用者快照
    pruned_dangling: List[Reference]
    pruned_expired: List[Reference]

    def to_dict(self) -> dict:
        return {
            "reclaimed_count": len(self.reclaimed),
            "retained_count": len(self.retained),
            "reclaimed": self.reclaimed,
            "retained": self.retained,
            "pruned_dangling_count": len(self.pruned_dangling),
            "pruned_expired_count": len(self.pruned_expired),
            "pruned_dangling": [r.__dict__ for r in self.pruned_dangling],
            "pruned_expired": [r.__dict__ for r in self.pruned_expired],
        }


def garbage_collect(reg: VolumeRegistry, now: float = 0.0) -> GCReport:
    """回收流程（确定性、可校验）：
    1. sweep：摘除悬空引用与过期临时挂载；
    2. 判定：对每个仍登记的卷取有效引用者快照；
    3. 回收：零有效引用者的卷删除并放入 reclaimed；
    4. 其余放入 retained。校验数据见 verify()。
    """
    dangling, expired = reg.sweep(now)
    referrers_of = {v: reg.live_referrers(v, now) for v in reg.volumes}
    reclaimed = sorted(v for v, refs in referrers_of.items() if not refs)
    retained = sorted(v for v, refs in referrers_of.items() if refs)
    for v in reclaimed:
        reg.delete_volume(v)
    return GCReport(reclaimed, retained, referrers_of, dangling, expired)


def verify(reg: VolumeRegistry, report: GCReport, now: float = 0.0) -> dict:
    """回收校验。两条不变量必须全部成立：
      A. 每个已回收卷：回收时刻有效引用者数 == 0（依据快照），且已不在登记册；
      B. 每个未回收卷：回收时刻有效引用者数 >= 1，且仍在登记册。
    """
    bad_reclaimed = [v for v in report.reclaimed
                     if report.referrers_of.get(v) or v in reg.volumes]
    bad_retained = [v for v in report.retained
                     if not report.referrers_of.get(v) or v not in reg.volumes]
    # 双向计数一致：所有卷恰好落入两类之一
    partition_ok = (len(report.reclaimed) + len(report.retained)
                    == len(report.referrers_of))
    ok = not bad_reclaimed and not bad_retained and partition_ok
    return {
        "ok": ok,
        "invariant_A_reclaimed_have_zero_referrers": not bad_reclaimed,
        "invariant_B_retained_have_at_least_one": not bad_retained,
        "invariant_C_partition_exhaustive": partition_ok,
        "reclaimed_checked": len(report.reclaimed),
        "retained_checked": len(report.retained),
        "violations": {"reclaimed": bad_reclaimed, "retained": bad_retained},
    }
