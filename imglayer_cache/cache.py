"""分层缓存：以层缓存键（layer key）为索引的内容寻址层存储。

每个缓存条目保存：
  key           层缓存键（H(父键, 指令指纹)）
  diff_digest   层差异摘要（声称值，取用时必须与重放结果一致）
  blob          层差异的确定性序列化字节（内容本身）
  op_kind       指令类型（workdir/env 无文件系统差异）
  workdir/env   命中后需要恢复的元数据状态

取用时会重新计算 blob 的差异摘要并与声称值比对：blob 被篡改会被发现，
从而避免“错误复用”。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .builder import BuildLayer
from .hashing import diff_digest
from .model import FileRecord


def serialize_changes(changes: dict[str, FileRecord | None]) -> bytes:
    """层差异的确定性 JSON 序列化（键排序，禁止非 ASCII 转义）。"""
    payload = {}
    for path in sorted(changes):
        record = changes[path]
        if record is None:
            payload[path] = None
        else:
            payload[path] = {
                "content": record.content.hex(),
                "mode": record.mode,
                "kind": record.kind,
                "target": record.target.hex() if record.target is not None else None,
            }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def deserialize_changes(blob: bytes) -> dict[str, FileRecord | None]:
    payload = json.loads(blob.decode("utf-8"))
    changes: dict[str, FileRecord | None] = {}
    for path, item in payload.items():
        if item is None:
            changes[path] = None
        else:
            target = bytes.fromhex(item["target"]) if item["target"] is not None else None
            changes[path] = FileRecord(
                content=bytes.fromhex(item["content"]),
                mode=item["mode"],
                kind=item["kind"],
                target=target,
            )
    return changes


@dataclass
class CacheEntry:
    key: str
    diff_digest_value: str
    blob: bytes
    op_kind: str
    workdir: str
    env: dict[str, str] = field(default_factory=dict)

    def is_intact(self) -> bool:
        """重新计算 blob 摘要，检测内容是否被损坏/篡改。"""
        try:
            changes = deserialize_changes(self.blob)
        except Exception:
            return False
        return diff_digest(changes) == self.diff_digest_value


class LayerCache:
    def __init__(self) -> None:
        self._entries: dict[str, CacheEntry] = {}

    def populate(
        self,
        layer: BuildLayer,
        workdir: str,
        env: dict[str, str],
    ) -> CacheEntry:
        blob = serialize_changes(layer.changes)
        entry = CacheEntry(
            key=layer.key,
            diff_digest_value=layer.diff_digest_value,
            blob=blob,
            op_kind=layer.op.kind,
            workdir=workdir,
            env=dict(env),
        )
        self._entries[layer.key] = entry
        return entry

    def lookup(self, key: str) -> CacheEntry | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if not entry.is_intact():
            raise RuntimeError(f"缓存条目损坏（差异摘要不匹配）: {key[:16]}...")
        return entry

    def debug_replace_blob(self, key: str, blob: bytes) -> None:
        """测试辅助：替换某条目的内容字节（模拟缓存损坏）。"""
        self._entries[key].blob = blob

    def __len__(self) -> int:
        return len(self._entries)
