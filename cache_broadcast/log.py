"""失效消息的持久追加日志。

对应真实系统里的「持久化消息总线 / 预写日志」：失效先落日志再广播，
因此任意历史区间都可以被重放（replay），这是丢包补齐与重启收敛的基础。
"""

import json
import os
import struct
from typing import List, Optional

from .model import Invalidation

_MAGIC = b"CBIL"  # cache broadcast invalidation log
_VERSION = 1
_HEADER = struct.Struct("<4sI")  # magic, format version
_RECORD = struct.Struct("<IIq")  # key_len, version, seq


class DurableLog:
    """append-only 失效日志；可选落盘，支持按区间重放。"""

    def __init__(self, path: Optional[str] = None):
        self.path = path
        self._records: List[Invalidation] = []  # records[seq-1]
        self._fh = None
        if path is not None:
            existed = os.path.exists(path) and os.path.getsize(path) > 0
            if existed:
                self._load(path)
            self._fh = open(path, "ab")
            if not existed:
                self._fh.write(_HEADER.pack(_MAGIC, _VERSION))
                self._fh.flush()
                os.fsync(self._fh.fileno())

    def _load(self, path: str) -> None:
        with open(path, "rb") as fh:
            header = fh.read(_HEADER.size)
            if not header:
                return
            magic, _version = _HEADER.unpack(header)
            if magic != _MAGIC:
                raise ValueError(f"not a invalidation log: {path}")
            while True:
                raw = fh.read(_RECORD.size)
                if not raw:
                    break
                key_len, version, seq = _RECORD.unpack(raw)
                key = fh.read(key_len).decode("utf-8")
                self._records.append(Invalidation(seq=seq, key=key, version=version))

    def append(self, key: str, version: int) -> Invalidation:
        """追加一条失效，返回带 seq 的消息。seq 从 1 开始严格连续。"""
        inv = Invalidation(seq=len(self._records) + 1, key=key, version=version)
        self._records.append(inv)
        if self._fh is not None:
            key_b = key.encode("utf-8")
            self._fh.write(_RECORD.pack(len(key_b), version, inv.seq))
            self._fh.write(key_b)
            self._fh.flush()
            os.fsync(self._fh.fileno())
        return inv

    def replay(self, from_seq: int, to_seq: Optional[int] = None) -> List[Invalidation]:
        """重放闭区间 [from_seq, to_seq]；to_seq 缺省为重放到日志末尾。"""
        if to_seq is None or to_seq > len(self._records):
            to_seq = len(self._records)
        if from_seq < 1:
            from_seq = 1
        if from_seq > to_seq:
            return []
        return self._records[from_seq - 1 : to_seq]

    def replay_seqs(self, seqs) -> List[Invalidation]:
        """按给定序号集合重放（用于精确补齐空洞）。"""
        records = self._records
        return [records[s - 1] for s in seqs if 1 <= s <= len(records)]

    def __len__(self) -> int:
        return len(self._records)

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
