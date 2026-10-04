"""权限模型。

- 每个候选名称属于一个权限组 ``group``；``None`` 表示对所有人公开。
- 每个用户持有若干已授权组 ``groups``（不可变集合）。
- 名称可见的充要条件：组为 ``None``，或用户被授予了该组，或用户是超级用户。

权限"在补全过程中发生变化"时的确定性语义：

1. ``ACL.snapshot()`` 返回某一时刻的 *不可变* ``Snapshot``（带整数版本号
   ``epoch``）。一次补全（或一个 ``Session`` 内的多次补全）自始至终使用
   同一个快照，因此补全进行期间权限如何变化都不会影响本次结果——返回的每
   一个名字都按同一个版本做过权限判定。
2. ``ACL`` 内部用读写锁保护：补全持读锁（相互并发），``set_groups`` /
   ``grant`` / ``revoke`` 持写锁（与补全互斥，且整个替换是原子的）。要么整
   个结果基于旧权限，要么基于新权限，不会出现"半旧半新"。
3. 更新只替换快照引用，旧快照不会被原地修改，已经开始的补全可安全跑完。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import FrozenSet, Iterable, Optional, Set, Tuple, Union

GroupId = Optional[int]


@dataclass(frozen=True)
class Snapshot:
    """某一时刻的不可变权限视图。"""

    epoch: int
    grants: dict = field(default_factory=dict)  # user_id -> frozenset(group)
    superusers: frozenset = frozenset()

    def groups_of(self, user_id: str) -> FrozenSet[int]:
        return self.grants.get(user_id, frozenset())

    def is_superuser(self, user_id: str) -> bool:
        return user_id in self.superusers

    def visible(self, group: GroupId, user_id: str) -> bool:
        """名称对用户是否可见。公开组永远可见。"""
        if group is None:
            return True
        if user_id in self.superusers:
            return True
        return group in self.grants.get(user_id, frozenset())


class _RWLock:
    """可重入的读者-写者锁（写优先，避免写者饥饿）。"""

    def __init__(self) -> None:
        self._cond = threading.Condition(threading.Lock())
        self._readers = 0
        self._waiting_writers = 0
        self._writer = False
        self._owner = 0

    def acquire_read(self) -> None:
        tid = threading.get_ident()
        with self._cond:
            if self._owner == tid and self._writer:
                # 持有写锁的线程再次获取读锁，直接放行（可重入）。
                return
            while self._writer or self._waiting_writers:
                self._cond.wait()
            self._readers += 1

    def release_read(self) -> None:
        tid = threading.get_ident()
        with self._cond:
            if self._owner == tid and self._writer:
                return
            self._readers -= 1
            if self._readers == 0:
                self._cond.notify_all()

    def acquire_write(self) -> None:
        tid = threading.get_ident()
        with self._cond:
            if self._owner == tid and self._writer:
                return
            self._waiting_writers += 1
            try:
                while self._writer or self._readers > 0:
                    self._cond.wait()
                self._writer = True
                self._owner = tid
            finally:
                self._waiting_writers -= 1

    def release_write(self) -> None:
        with self._cond:
            self._writer = False
            self._owner = 0
            self._cond.notify_all()

    @property
    def reader(self) -> "_ReadContext":
        return _ReadContext(self)

    @property
    def writer(self) -> "_WriteContext":
        return _WriteContext(self)


class _ReadContext:
    def __init__(self, lock: "_RWLock") -> None:
        self._lock = lock

    def __enter__(self) -> "_ReadContext":
        self._lock.acquire_read()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._lock.release_read()


class _WriteContext:
    def __init__(self, lock: "_RWLock") -> None:
        self._lock = lock

    def __enter__(self) -> "_WriteContext":
        self._lock.acquire_write()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._lock.release_write()


class ACL:
    """线程安全的权限存储。更新原子、版本递增。"""

    def __init__(self) -> None:
        self._lock = _RWLock()
        self._snapshot = Snapshot(epoch=0)

    # ---- 读 -------------------------------------------------------------
    def snapshot(self) -> Snapshot:
        """获取当前权限快照（与任何并发更新互斥，版本确定）。"""
        with self._lock.reader:
            return self._snapshot

    # ---- 写 -------------------------------------------------------------
    def set_groups(
        self,
        grants: Optional[dict] = None,
        superusers: Optional[Iterable[str]] = None,
    ) -> Snapshot:
        """整批替换授权（或只替换其中一项），原子递增版本号。"""
        with self._lock.writer:
            old = self._snapshot
            new_grants = old.grants if grants is None else {
                uid: frozenset(g) for uid, g in grants.items()
            }
            new_supers = old.superusers if superusers is None else frozenset(superusers)
            snap = Snapshot(old.epoch + 1, new_grants, new_supers)
            self._snapshot = snap
            return snap

    def grant(self, user_id: str, groups: Iterable[int]) -> Snapshot:
        with self._lock.writer:
            old = self._snapshot
            merged: Set[int] = set(old.grants.get(user_id, frozenset()))
            merged.update(groups)
            new_grants = dict(old.grants)
            new_grants[user_id] = frozenset(merged)
            snap = Snapshot(old.epoch + 1, new_grants, old.superusers)
            self._snapshot = snap
            return snap

    def revoke(self, user_id: str, groups: Iterable[int]) -> Snapshot:
        with self._lock.writer:
            old = self._snapshot
            current = old.grants.get(user_id, frozenset())
            removed = frozenset(groups)
            remaining = frozenset(g for g in current if g not in removed)
            if not remaining:
                new_grants = {u: gg for u, gg in old.grants.items() if u != user_id}
            else:
                new_grants = dict(old.grants)
                new_grants[user_id] = remaining
            snap = Snapshot(old.epoch + 1, new_grants, old.superusers)
            self._snapshot = snap
            return snap
