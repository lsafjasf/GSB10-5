"""最终一致性校验数据（digest）。

系统收敛的判据分两部分，缺一不可：
1. 失效状态 digest：所有节点 applied_seq 相同、每键失效版本下界相同。
2. 脏缓存计数：每个节点缓存中版本 < 其失效下界的条目数必须为 0。
（合法的高版本缓存内容可以各节点不同，故不纳入 digest。）

digest 对 watermark 项做顺序无关的 XOR 聚合，避免在大键空间下排序，
同时保持与顺序相关哈希同等的碰撞防护。
"""

import hashlib
from typing import Dict

from .node import CacheNode

_DIGEST_SIZE = 16


def _item_digest(key: str, version: int) -> bytes:
    h = hashlib.blake2b(digest_size=_DIGEST_SIZE)
    h.update(repr(key).encode())
    h.update(b":")
    h.update(str(version).encode())
    return h.digest()


def state_digest(node: CacheNode) -> str:
    acc = bytearray(_DIGEST_SIZE)
    for key, version in node.watermarks.items():
        item = _item_digest(key, version)
        for i in range(_DIGEST_SIZE):
            acc[i] ^= item[i]
    h = hashlib.blake2b(digest_size=_DIGEST_SIZE)
    h.update(b"applied=")
    h.update(str(node.applied_seq).encode())
    h.update(b";max_seen=")
    h.update(str(node.max_seen).encode())
    h.update(b";wm_acc=")
    h.update(bytes(acc))
    return h.hexdigest()


def count_stale_entries(node: CacheNode) -> int:
    """统计缓存中低于失效版本下界的脏条目数；收敛后应为 0。"""
    stale = 0
    wm = node.watermarks
    for key, (_value, version) in node.cache.items():
        bound = wm.get(key, 0)
        if version < bound:
            stale += 1
    return stale


def cluster_summary(nodes) -> Dict[str, object]:
    digests = {state_digest(n) for n in nodes}
    applied = {n.applied_seq for n in nodes}
    max_seen_set = {n.max_seen for n in nodes}
    stale = {n.node_id: count_stale_entries(n) for n in nodes}
    return {
        "converged": len(digests) == 1,
        "digest_count": len(digests),
        "applied_seq_range": [min(applied), max(applied)],
        "max_seen_range": [min(max_seen_set), max(max_seen_set)],
        "stale_total": sum(stale.values()),
        "stale_per_node": stale,
        "digest": next(iter(digests)) if len(digests) == 1 else None,
    }
