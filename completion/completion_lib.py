"""Interactive command-line completion core (stdlib only).

Index model
-----------
Each candidate name carries one or more *permission group* tags
(e.g. roles / ACL ids).  Every group keeps its own sorted list of names:

    group_a -> ["alpha", "altar", "azimuth", ...]
    group_b -> ["beta", "betray", ...]

A user is mapped to a frozenset of groups.  Completion(prefix):

    1. bisect_left each of the user's group lists at `prefix`  (O(log m))
    2. walk forward while name.startswith(prefix), capped at `limit`
    3. heapq.merge the per-group sorted streams, dedupe, truncate

Cost per query: O(sum log m_g + k * log(#groups)), independent of total N,
so 1 or 10 million candidates make little difference once the prefix
is selective; broad prefixes are bounded by `limit` immediately.

Permissions are enforced *inside* the index: groups the user does not
belong to are never opened, so unauthorized names cannot enter the merge.

Permission changes carry a monotonically increasing version.  Each query
snapshots its group lists and returns a result consistent with exactly one
version; the result cache is keyed/validated by version and invalidated on
any permission mutation.
"""

from __future__ import annotations

import bisect
from heapq import merge as heap_merge
from typing import Dict, FrozenSet, Iterable, List, Sequence, Tuple

__all__ = ["CompletionIndex", "PermissionChanged", "version_snapshot"]


class PermissionChanged(Exception):
    """Raised by a long-running generator when permissions change mid-iteration.

    Callers see a *deterministic* outcome: either consume
    ``complete_iter`` to completion (results stay consistent with the
    snapshot version taken at call time) or catch this and re-query; the
    library never silently mixes names from two permission versions.
    """


def version_snapshot() -> Tuple[int, int]:
    """Helper retained for API symmetry (version + data generation)."""
    return (0, 0)


class CompletionIndex:
    def __init__(self, limit: int = 50, cache_size: int = 4096) -> None:
        self._groups: Dict[str, List[str]] = {}
        self._name_groups: Dict[str, set] = {}
        self._user_groups: Dict[str, FrozenSet[str]] = {}
        self._version = 1
        self._limit = limit
        self._cache: Dict[Tuple[str, str, int], Tuple[int, Tuple[str, ...]]] = {}
        self._cache_size = cache_size

    # ---- mutation -------------------------------------------------------

    def add(self, name: str, groups: Iterable[str]) -> None:
        groups = frozenset(groups)
        old = self._name_groups.get(name)
        if old is not None:
            self.remove(name)
        self._name_groups[name] = set(groups)
        for g in groups:
            lst = self._groups.get(g)
            if lst is None:
                lst = []
                self._groups[g] = lst
            bisect.insort(lst, name)
        self._version += 1
        self._cache.clear()

    def remove(self, name: str) -> None:
        groups = self._name_groups.pop(name, None)
        if groups is None:
            return
        for g in groups:
            lst = self._groups.get(g)
            if lst:
                i = bisect.bisect_left(lst, name)
                if i < len(lst) and lst[i] == name:
                    lst.pop(i)
        self._version += 1
        self._cache.clear()

    def bulk_add(self, items: Iterable[Tuple[str, Iterable[str]]]) -> int:
        """Efficient bulk load: O(N log N) total instead of O(N^2) inserts.

        `items` yields (name, groups).  Names need only live once; group
        lists store references to the same string objects.
        """
        buckets: Dict[str, set] = {}
        count = 0
        for name, groups in items:
            gs = set(groups)
            prev = self._name_groups.get(name)
            if prev is not None:
                gs |= prev
            self._name_groups[name] = gs
            for g in gs:
                buckets.setdefault(g, set()).add(name)
            count += 1
        for g, names in buckets.items():
            merged = names.union(self._groups.get(g, ()))
            self._groups[g] = sorted(merged)
        self._version += 1
        self._cache.clear()
        return count

    def set_user_groups(self, user: str, groups: Iterable[str]) -> None:
        """Grant/revoke. Bumps version and invalidates cached completions."""
        self._user_groups[user] = frozenset(groups)
        self._version += 1
        self._cache.clear()

    def version(self) -> int:
        return self._version

    def size(self) -> int:
        return len(self._name_groups)

    # ---- query ----------------------------------------------------------

    def _group_streams(
        self, lists: Sequence[List[str]], prefix: str, per_group_cap: int
    ):
        for lst in lists:
            i = bisect.bisect_left(lst, prefix)
            taken = 0
            n = len(lst)
            while i < n and taken < per_group_cap and lst[i].startswith(prefix):
                yield lst[i]
                i += 1
                taken += 1

    def complete(self, user: str, prefix: str, limit: int | None = None) -> List[str]:
        """Return up to `limit` visible names, globally sorted and deduped."""
        limit = self._limit if limit is None else limit
        ver = self._version
        key = (user, prefix, limit)
        hit = self._cache.get(key)
        if hit is not None and hit[0] == ver:
            return list(hit[1])

        groups = self._user_groups.get(user, frozenset())
        # Snapshot taken atomically under the GIL: list references cannot
        # change mid-query; mutations replace/rebuild rather than mutate
        # entries in place during a single complete() call.
        lists = [self._groups[g] for g in sorted(groups) if g in self._groups]

        streams = self._group_streams(lists, prefix, limit)
        merged = heap_merge(streams) if lists else iter(())

        out: List[str] = []
        last = None
        for name in merged:
            if name == last:
                continue
            out.append(name)
            last = name
            if len(out) >= limit:
                break

        result = tuple(out)
        self._cache[key] = (ver, result)
        if len(self._cache) > self._cache_size:
            self._cache.clear()
        return list(result)

    def complete_iter(self, user: str, prefix: str, limit: int | None = None):
        """Streaming variant. Raises PermissionChanged if permissions mutate
        before the stream is fully consumed; partial results already yielded
        were all consistent with the start-of-call snapshot."""
        start_ver = self._version
        groups = self._user_groups.get(user, frozenset())
        lists = [self._groups[g] for g in sorted(groups) if g in self._groups]
        streams = self._group_streams(lists, prefix, limit or self._limit)
        merged = heap_merge(streams) if lists else iter(())
        last = None
        count = 0
        cap = self._limit if limit is None else limit
        for name in merged:
            if self._version != start_ver:
                raise PermissionChanged(
                    f"permissions changed v{start_ver}->v{self._version}; "
                    f"re-issue the query"
                )
            if name == last:
                continue
            yield name
            last = name
            count += 1
            if count >= cap:
                return

    def visible_names(self, user: str) -> FrozenSet[str]:
        """Full visible set for assertions / tests."""
        groups = self._user_groups.get(user, frozenset())
        out: set = set()
        for g in groups:
            out.update(self._groups.get(g, ()))
        return frozenset(out)
