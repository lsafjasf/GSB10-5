"""缓存失效广播库（仅标准库）。

核心抽象：
- Invalidation: 一条带全局单调序号 seq 与版本号 version 的失效消息。
- DurableLog: 失效消息的持久追加日志（真实组件的替身，见 SEMANTICS.md）。
- CacheNode: 缓存节点，乱序/重复安全地处理失效，并可从日志补齐。
"""

from .model import Invalidation
from .log import DurableLog
from .node import CacheNode
from .cluster import Cluster
from .digest import state_digest, count_stale_entries

__all__ = [
    "Invalidation",
    "DurableLog",
    "CacheNode",
    "Cluster",
    "state_digest",
    "count_stale_entries",
]
