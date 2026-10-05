"""集群模拟器：广播 fan-out、丢失/重复投递、反熵补齐（anti-entropy）。

投递模型：
- fanout:  每条失效按目标节点集合投递，可注入丢失率与重复率（含乱序）。
- 反熵轮： 节点用 missing_seqs() 自查缺口，向持久日志请求精确重放；
           重放消息本身也可丢失，需要多轮才能收敛——这正是「广播丢失
           最终收敛」的补齐机制。
"""

import random
from typing import Dict, Iterable, List, Optional

from .log import DurableLog
from .model import Invalidation
from .node import CacheNode


class Cluster:
    def __init__(self, log: DurableLog, seed: int = 12345):
        self.log = log
        self.nodes: Dict[str, CacheNode] = {}
        self.rng = random.Random(seed)
        self.delivered = 0
        self.duplicates_sent = 0
        self.lost = 0

    def add(self, node: CacheNode) -> None:
        self.nodes[node.node_id] = node

    # ---------------- 广播 ----------------

    def fanout(
        self,
        invs: Iterable[Invalidation],
        loss: float = 0.0,
        duplicate: float = 0.0,
        targets: Optional[List[str]] = None,
        shuffle: bool = False,
    ) -> None:
        """把一批失效广播给节点，按概率丢失 / 重复。"""
        node_ids = targets if targets is not None else list(self.nodes)
        rng = self.rng
        deliveries: List[tuple] = []
        for inv in invs:
            for nid in node_ids:
                if rng.random() < loss:
                    self.lost += 1
                    continue
                deliveries.append((nid, inv))
                if rng.random() < duplicate:
                    deliveries.append((nid, inv))
                    self.duplicates_sent += 1
        if shuffle:
            rng.shuffle(deliveries)
        for nid, inv in deliveries:
            self.nodes[nid].handle(inv)
            self.delivered += 1

    # ---------------- 反熵补齐 ----------------

    def anti_entropy_round(
        self,
        replay_loss: float = 0.0,
        replay_duplicate: float = 0.0,
        batch_limit: Optional[int] = None,
    ) -> int:
        """一轮反熵：所有节点向日志拉取缺口并应用。返回本轮实际应用数。"""
        rng = self.rng
        applied_total = 0
        log_len = len(self.log)
        for node in list(self.nodes.values()):
            want = node.missing_seqs(batch_limit)
            room = None
            if batch_limit is not None:
                room = batch_limit - len(want)
            tail_from = max(node.applied_seq, node.max_seen) + 1
            tail = self.log.replay(tail_from)
            if room is not None:
                tail = tail[:room]
            payload = self.log.replay_seqs(want) + tail
            rng.shuffle(payload)
            for inv in payload:
                if rng.random() < replay_loss:
                    self.lost += 1
                    continue
                if node.handle(inv):
                    applied_total += 1
                self.delivered += 1
                if rng.random() < replay_duplicate:
                    node.handle(inv)  # 立即重投一次：必须幂等
                    self.duplicates_sent += 1
                    self.delivered += 1
        return applied_total

    def converge(
        self,
        replay_loss: float = 0.0,
        replay_duplicate: float = 0.0,
        max_rounds: int = 100,
        batch_limit: Optional[int] = None,
    ) -> List[int]:
        """反复执行反熵直到所有节点 applied_seq == len(log)。返回每轮应用数。"""
        rounds: List[int] = []
        target = len(self.log)
        for _ in range(max_rounds):
            if all(n.applied_seq == target for n in self.nodes.values()):
                break
            rounds.append(
                self.anti_entropy_round(
                    replay_loss=replay_loss,
                    replay_duplicate=replay_duplicate,
                    batch_limit=batch_limit,
                )
            )
        return rounds
