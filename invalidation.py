"""Versioned cache-invalidation broadcast (Python 3 standard library only).

消息模型:  Message(origin, seq, key, version)
  - origin:  发送方(失效源)ID
  - seq:     该 origin 内严格单调、连续的序号 (1, 2, 3, ...)
  - key:     被失效的缓存键
  - version: 失效版本号, 来自全局单调时钟(生产环境可用 HLC / 数据存储版本)

语义:
  - 失效消息表示 "key 上所有 write_version <= version 的缓存条目全部作废"。
  - 新写入总是拿到比任何已发出失效号更大的版本, 因此即使旧失效被延迟/重复送达,
    也不会清掉新数据 (驱逐以版本比较为准, 而不是 "看到消息就删")。
  - 同一 (origin, seq) 无论收到多少次, 效果完全一致:
      1) 序号 <= 已连续应用序号 -> 直接丢弃;
      2) 即使重复应用, 版本表取 max、驱逐带版本守卫, 仍然是 no-op。
"""

from __future__ import annotations

import json
import os
from array import array
from collections import namedtuple
from typing import Any, Dict, Iterable, List, Optional

Message = namedtuple("Message", ["origin", "seq", "key", "version"])


class MonotonicClock:
    """全局单调逻辑时钟, 模拟生产环境的 HLC / 数据库版本号。"""

    def __init__(self, start: int = 0) -> None:
        self._t = start

    def tick(self) -> int:
        self._t += 1
        return self._t

    def now(self) -> int:
        return self._t


class DictVersionTable:
    """key -> 已收到的最大失效版本 (通用哈希键)。"""

    kind = "dict"

    def __init__(self) -> None:
        self.data: Dict[Any, int] = {}

    def get(self, key: Any) -> int:
        return self.data.get(key, 0)

    def merge(self, key: Any, version: int) -> bool:
        old = self.data.get(key)
        if old is None or version > old:
            self.data[key] = version
            return True
        return False

    def snapshot(self) -> List[list]:
        return [[k, v] for k, v in self.data.items()]

    def restore(self, pairs: Iterable[list]) -> None:
        self.data = {k: v for k, v in pairs}


class ArrayVersionTable:
    """紧凑版本表: 整数键 0..size-1, 版本为 uint64。

    大规模场景下每个节点的内存占用约为 size * 8 字节。
    """

    kind = "array"

    def __init__(self, size: int) -> None:
        self.size = size
        self.data = array("Q", bytes(8 * size))

    def get(self, key: int) -> int:
        return self.data[key]

    def merge(self, key: int, version: int) -> bool:
        d = self.data
        if version > d[key]:
            d[key] = version
            return True
        return False

    def snapshot(self) -> List[list]:
        return [[i, v] for i, v in enumerate(self.data) if v]

    def restore(self, pairs: Iterable[list]) -> None:
        data = array("Q", bytes(8 * self.size))
        for k, v in pairs:
            data[int(k)] = int(v)
        self.data = data


# 每个 origin 的乱序缓冲上限; 超过后不再缓冲, 完全交给反熵补齐
BUFFER_CAP_PER_ORIGIN = 4096


class CacheNode:
    def __init__(self, node_id: Any, table: Optional[object] = None) -> None:
        self.node_id = node_id
        self.table = table if table is not None else DictVersionTable()
        self.cache: Dict[Any, tuple] = {}          # key -> (value, write_version)
        self.contig: Dict[Any, int] = {}          # origin -> 已连续应用到的 seq
        self.buffered: Dict[Any, Dict[int, Message]] = {}  # origin -> {seq: msg}
        # 统计
        self.applied = 0
        self.dups_dropped = 0
        self.overrun_dropped = 0
        self.repaired = 0
        self.stale_writes_rejected = 0

    # ---------------------------------------------------------------- 写入/读取

    def write(self, key: Any, value: Any, version: int) -> bool:
        """写入缓存。version 必须大于该 key 已知的最大失效版本, 否则视为过期写。"""
        if version <= self.table.get(key):
            self.stale_writes_rejected += 1
            return False
        self.cache[key] = (value, version)
        return True

    def read(self, key: Any):
        entry = self.cache.get(key)
        if entry is None:
            return None
        value, version = entry
        if version <= self.table.get(key):  # 惰性驱逐: 读时发现已失效
            del self.cache[key]
            return None
        return value

    def stale_entries(self) -> List[Any]:
        """全量扫描, 返回所有理应已被失效但还残留在 cache 里的键 (校验用)。"""
        return [
            key
            for key, (_, version) in self.cache.items()
            if version <= self.table.get(key)
        ]

    # ---------------------------------------------------------------- 消息接收

    def receive(self, msg: Message) -> str:
        """处理一条消息, 返回 'applied' | 'duplicate' | 'buffered' | 'overrun'。"""
        cur = self.contig.get(msg.origin, 0)
        if msg.seq <= cur:
            self.dups_dropped += 1
            return "duplicate"
        buf = self.buffered.get(msg.origin)
        if buf is not None and msg.seq in buf:
            self.dups_dropped += 1
            return "duplicate"
        if msg.seq == cur + 1:
            self._apply(msg)
            cur += 1
            if buf:
                nxt = cur + 1
                while nxt in buf:
                    self._apply(buf.pop(nxt))
                    cur, nxt = cur + 1, nxt + 1
                if not buf:
                    del self.buffered[msg.origin]
            self.contig[msg.origin] = cur
            return "applied"
        # 乱序/丢消息造成缺口: 缓冲, 等缺口补齐; 超容时靠反熵
        if buf is None:
            buf = {}
            self.buffered[msg.origin] = buf
        if len(buf) < BUFFER_CAP_PER_ORIGIN:
            buf[msg.seq] = msg
            return "buffered"
        self.overrun_dropped += 1
        return "overrun"

    def receive_batch(self, msgs: List[Message]) -> None:
        """批量接收同一 origin 的有序消息 (含连续送达的快路径)。"""
        if not msgs:
            return
        first = msgs[0]
        cur = self.contig.get(first.origin, 0)
        if (
            first.seq == cur + 1
            and msgs[-1].seq == cur + len(msgs)
            and not self.buffered.get(first.origin)
        ):
            for m in msgs:
                self._apply(m)
            self.contig[first.origin] = cur + len(msgs)
            return
        for m in msgs:
            self.receive(m)

    def _apply(self, msg: Message) -> None:
        self.table.merge(msg.key, msg.version)
        entry = self.cache.get(msg.key)
        if entry is not None and entry[1] <= msg.version:
            del self.cache[msg.key]
        self.applied += 1

    # ---------------------------------------------------------------- 反熵补齐

    def digest(self) -> Dict[Any, int]:
        """该节点已连续应用到的各 origin 序号, 用于和对端比对缺口。"""
        return dict(self.contig)

    def need_from(self, origin: "Origin") -> int:
        return origin.seq - self.contig.get(origin.id, 0)

    def anti_entropy(self, origins: Iterable["Origin"]) -> int:
        """向各失效源的持久日志拉取自己缺失的全部消息 (store-and-forward)。

        生产环境中同样可以由持有日志的 peer 提供; 拉取基于 digest 序号差,
        已经在缓冲里的消息由 receive() 自动去重。
        """
        pulled = 0
        for origin in origins:
            cur = self.contig.get(origin.id, 0)
            if origin.seq > cur:
                for msg in origin.fetch(cur + 1, origin.seq + 1):
                    before = self.applied
                    self.receive(msg)
                    if self.applied > before:
                        self.repaired += 1
                        pulled += 1
        return pulled

    # ---------------------------------------------------------------- 持久化/重启

    def dump(self, path: str) -> None:
        """原子落盘节点状态 (重启点)。cache value 需 JSON 可序列化。"""
        buffered = [
            [o, s, m.key, m.version]
            for o, buf in self.buffered.items()
            for s, m in buf.items()
        ]
        state = {
            "node_id": self.node_id,
            "table_kind": self.table.kind,
            "table_size": getattr(self.table, "size", None),
            "inv_table": self.table.snapshot(),
            "cache": [[k, v, ver] for k, (v, ver) in self.cache.items()],
            "contig": [[o, s] for o, s in self.contig.items()],
            "buffered": buffered,
            "stats": {
                "applied": self.applied,
                "dups_dropped": self.dups_dropped,
                "overrun_dropped": self.overrun_dropped,
                "repaired": self.repaired,
                "stale_writes_rejected": self.stale_writes_rejected,
            },
        }
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: str) -> "CacheNode":
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
        if state["table_kind"] == "array":
            table = ArrayVersionTable(state["table_size"])
        else:
            table = DictVersionTable()
        node = cls(state["node_id"], table=table)
        node.table.restore(state["inv_table"])
        node.cache = {
            k: (v, ver) for k, v, ver in state["cache"]
        }
        node.contig = {o: s for o, s in state["contig"]}
        for o, s, key, version in state["buffered"]:
            node.buffered.setdefault(o, {})[int(s)] = Message(o, int(s), key, version)
        for name, val in state["stats"].items():
            setattr(node, name, val)
        return node


class Origin:
    """失效消息源: 维护单调 seq 与持久化重放日志 (store-and-forward)。"""

    def __init__(self, origin_id: Any) -> None:
        self.id = origin_id
        self.seq = 0
        self.log: List[Message] = []

    def invalidate(self, key: Any, version: int) -> Message:
        self.seq += 1
        msg = Message(self.id, self.seq, key, version)
        self.log.append(msg)
        return msg

    def fetch(self, start_seq: int, stop_seq: int) -> List[Message]:
        """供反熵拉取: 返回 seq ∈ [start_seq, stop_seq) 的消息。"""
        lo = max(1, start_seq) - 1
        hi = min(self.seq + 1, stop_seq) - 1
        return self.log[lo:hi] if hi > lo else []
