"""缓存节点：乱序/重复安全的失效处理 + 断点补齐 + 崩溃恢复。

节点状态：
- applied_seq: 已连续应用到的日志序号（高水位）。
- ahead:       已见但尚未连续（前面有空洞）的序号位图，乱序到达时置位，
               空洞补齐后批量转正。只记录「见过」，用于向日志请求精确补齐区间。
- max_seen:    见过的最大序号（含空洞后的）。
- watermarks:  每键已应用的失效版本下界（取历史最大，保证单调）。
- cache:       模拟缓存 {key: (value, version)}。

幂等/乱序/防误伤的不变量见 SEMANTICS.md。
"""

import json
import os
from typing import Dict, List, Optional, Tuple

from .log import DurableLog
from .model import Invalidation


class CacheNode:
    def __init__(self, node_id: str):
        self.node_id = node_id
        self.applied_seq = 0
        self.max_seen = 0
        self._ahead = bytearray()  # bit (seq-1) 置位 = 已见未连续
        self.watermarks: Dict[str, int] = {}
        self.cache: Dict[str, Tuple[object, int]] = {}
        # 统计
        self.dups_dropped = 0
        self.applied_count = 0

    # ---------------- 失效处理（幂等、乱序安全） ----------------

    def handle(self, inv: Invalidation) -> bool:
        """处理一条失效消息；返回 True 表示本次真正应用（非重复）。"""
        seq = inv.seq
        if seq <= self.applied_seq:
            self.dups_dropped += 1
            return False
        bit = seq - 1
        if seq <= self.max_seen:
            byte_i = bit >> 3
            if self._ahead[byte_i] >> (bit & 7) & 1:
                self.dups_dropped += 1
                return False
            self._ahead[byte_i] |= 1 << (bit & 7)
        else:
            need = (bit >> 3) + 1 - len(self._ahead)
            if need > 0:
                self._ahead.extend(b"\x00" * need)
            self._ahead[bit >> 3] |= 1 << (bit & 7)
            self.max_seen = seq
        self._apply(inv)
        if seq == self.applied_seq + 1:
            self.applied_seq = seq
            self._drain()
        return True

    def _apply(self, inv: Invalidation) -> None:
        key = inv.key
        ver = inv.version
        wm = self.watermarks.get(key)
        if wm is None or ver > wm:
            self.watermarks[key] = ver
        entry = self.cache.get(key)
        if entry is not None and entry[1] < ver:
            del self.cache[key]
        self.applied_count += 1

    def _drain(self) -> None:
        """空洞被补上后，把已缓存的后续序号连续转正。"""
        ahead = self._ahead
        seq = self.applied_seq
        max_seen = self.max_seen
        while seq < max_seen:
            bit = seq  # 下一个序号的 bit 下标
            if not (ahead[bit >> 3] >> (bit & 7) & 1):
                break
            ahead[bit >> 3] &= ~(1 << (bit & 7))
            seq += 1
        self.applied_seq = seq

    # ---------------- 补齐 / 追赶 ----------------

    def missing_seqs(self, limit: Optional[int] = None) -> List[int]:
        """返回 (applied_seq, max_seen] 内尚未收到的序号（空洞 + 尾部）。"""
        missing: List[int] = []
        ahead = self._ahead
        first_bit = self.applied_seq  # seq applied+1 的 bit 下标
        end_bit = self.max_seen
        byte_start = first_bit >> 3
        byte_end = (end_bit + 7) >> 3
        for i in range(byte_start, min(byte_end, len(ahead))):
            byte = ahead[i]
            if byte == 0xFF:
                continue
            base = i << 3
            for j in range(8):
                if not (byte >> j) & 1:
                    bit = base + j
                    if first_bit <= bit < end_bit:
                        missing.append(bit + 1)
        # 尾部：max_seen 之后到日志末尾的，由调用方按 limit 追加请求；
        # 这里只负责 (applied, max_seen] 区间。
        if limit is not None and len(missing) > limit:
            del missing[limit:]
        return missing

    def catchup(self, log: DurableLog, limit: Optional[int] = None) -> int:
        """从持久日志拉取并应用缺失的失效，返回本次新应用的条数。

        拉取范围 = (applied_seq, max_seen] 内的空洞 + 日志尾部新消息。
        重复消息由 handle() 幂等丢弃，因此本方法可任意重入。
        """
        want = self.missing_seqs(limit)
        room = None if limit is None else limit - len(want)
        tail_from = max(self.applied_seq, self.max_seen) + 1
        if room is None or room > 0:
            tail = log.replay(tail_from)
            if room is not None:
                tail = tail[:room]
        else:
            tail = []
        applied = 0
        for inv in log.replay_seqs(want):
            if self.handle(inv):
                applied += 1
        for inv in tail:
            if self.handle(inv):
                applied += 1
        return applied

    # ---------------- 缓存读写 ----------------

    def fill(self, key: str, value: object, version: int) -> bool:
        """回填缓存；版本低于该键失效下界的旧数据会被拒绝（防脏回填）。"""
        wm = self.watermarks.get(key, 0)
        if version < wm:
            return False
        self.cache[key] = (value, version)
        return True

    def get(self, key: str) -> Optional[object]:
        entry = self.cache.get(key)
        return None if entry is None else entry[0]

    # ---------------- 崩溃恢复 ----------------

    def checkpoint(self, path: str) -> None:
        """持久化恢复所需的最小状态（原子写入）。"""
        state = {
            "node_id": self.node_id,
            "applied_seq": self.applied_seq,
            "max_seen": self.max_seen,
            "watermarks": self.watermarks,
            "cache": {k: [v, ver] for k, (v, ver) in self.cache.items()},
        }
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

    @classmethod
    def restore(cls, path: str) -> "CacheNode":
        """从 checkpoint 恢复。空洞位图不必持久化：重启后通过
        catchup() 从日志重放 [applied_seq+1, 末尾]，重复部分自动幂等。"""
        with open(path, "r", encoding="utf-8") as fh:
            state = json.load(fh)
        node = cls(state["node_id"])
        node.applied_seq = state["applied_seq"]
        node.max_seen = state["max_seen"]
        node.watermarks = dict(state["watermarks"])
        node.cache = {k: (v, ver) for k, (v, ver) in state["cache"].items()}
        return node
