"""补全索引：排序数组 + 二分查找。

为什么不用 Trie（见 README.md 详述）：
- 100 万个候选时，Python dict 型 Trie 每节点开销巨大（每个 dict/节点上百字
  节，数百万节点 => GB 级内存）；
- 排序数组是最紧凑的不可变索引：100 万个字符串约 70 MB（字符串本体）+ 8 MB
  （指针数组）+ 4 MB（group 数组），二分只需约 log2(1e6) ≈ 20 次字符串比较；
- "前缀补全"在字典序数组中天然是一个连续区间 ``[lower_bound(prefix),
  lower_bound(prefix_sentinel))``，区间内顺序扫描并做权限过滤即可。

大量候选时如何保持快速：
1. 用 ``bisect_left`` 定位区间起点（C 实现，约 20 次比较）；
2. 扫描在拿到 ``limit`` 个 *可见* 结果后立即停止；
3. 空结果 / 不存在的前缀只需 O(log n)，与候选总量无关；
4. 权限判定在扫描循环中内联（frozenset 成员判断，O(1)），在结果返回之前完成；
5. 权限数据是不可变快照，补全期间无锁争用（快照获取本身被读锁保护，见 acl.py）。
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

from .acl import ACL, GroupId, Snapshot


@dataclass(frozen=True)
class CompletionResult:
    """一次补全的结果。

    items     可见的 (name, group) 对，按字典序
    names     仅名字的便捷视图
    truncated 是否因达到 limit 而截断（之后可能还有可见匹配）
    epoch     本次结果所依据的权限版本
    prefix    实际查询前缀
    scanned   扫描过的候选条数（含被权限过滤掉的），用于观测最坏情况
    """

    items: Tuple[Tuple[str, GroupId], ...]
    truncated: bool
    epoch: int
    prefix: str
    scanned: int

    @property
    def names(self) -> Tuple[str, ...]:
        return tuple(name for name, _ in self.items)


def _prefix_end(prefix: str) -> str:
    """字典序中所有以 prefix 开头的字符串的严格上界。"""
    return prefix[:-1] + chr(ord(prefix[-1]) + 1)


class CompletionIndex:
    """不可变补全索引（构建后候选集合不变；权限通过 ACL 独立演进）。"""

    __slots__ = ("_names", "_groups")

    def __init__(self, items: Iterable[Tuple[str, GroupId]]) -> None:
        ordered = sorted(items, key=lambda ng: ng[0])
        self._names: Tuple[str, ...] = tuple(name for name, _ in ordered)
        self._groups: Tuple[GroupId, ...] = tuple(group for _, group in ordered)

    def __len__(self) -> int:
        return len(self._names)

    def names(self) -> Tuple[str, ...]:
        return self._names

    def items(self) -> Tuple[Tuple[str, GroupId], ...]:
        return tuple(zip(self._names, self._groups))

    def session(self, acl: ACL) -> "Session":
        """获取一个权限快照会话。

        会话持有读锁直到退出，因此更新会阻塞、等待所有在途补全结束；会话内
        全部补全使用同一 epoch 的快照，语义确定。
        """
        return Session(self, acl)

    def complete(
        self,
        prefix: str,
        user_id: str,
        acl: ACL,
        limit: int = 10,
        snapshot: Optional[Snapshot] = None,
    ) -> CompletionResult:
        """单次补全；不传 snapshot 时取 ACL 当前快照。"""
        snap = snapshot if snapshot is not None else acl.snapshot()
        return self._scan(prefix, user_id, snap, limit)

    def _scan(
        self,
        prefix: str,
        user_id: str,
        snap: Snapshot,
        limit: int,
    ) -> CompletionResult:
        names = self._names
        groups = self._groups
        if not prefix:
            start = 0
            end = len(names)
        else:
            start = bisect_left(names, prefix)
            end = bisect_left(names, _prefix_end(prefix))

        grants = snap.grants.get(user_id, frozenset())
        is_super = user_id in snap.superusers

        out: List[Tuple[str, GroupId]] = []
        truncated = False
        i = start
        while i < end:
            group = groups[i]
            if is_super or group is None or group in grants:
                out.append((names[i], group))
                if len(out) >= limit:
                    i += 1
                    truncated = i < end
                    break
            i += 1
        scanned = i - start
        return CompletionResult(tuple(out), truncated, snap.epoch, prefix, scanned)


class Session:
    """绑定单个权限快照的一组补全（上下文管理器）。"""

    __slots__ = ("_index", "_acl", "_snapshot", "_token")

    def __init__(self, index: CompletionIndex, acl: ACL) -> None:
        self._index = index
        self._acl = acl
        self._snapshot: Optional[Snapshot] = None
        self._token = None

    def __enter__(self) -> "Session":
        self._token = self._acl._lock.reader
        self._token.__enter__()
        self._snapshot = self._acl.snapshot()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        assert self._token is not None
        self._token.__exit__(exc_type, exc, tb)

    @property
    def snapshot(self) -> Snapshot:
        assert self._snapshot is not None, "Session 必须在 with 块内使用"
        return self._snapshot

    @property
    def epoch(self) -> int:
        return self.snapshot.epoch

    def complete(
        self,
        prefix: str,
        user_id: str,
        limit: int = 10,
    ) -> CompletionResult:
        return self._index._scan(prefix, user_id, self.snapshot, limit)
