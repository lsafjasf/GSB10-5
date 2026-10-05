"""缓存失效广播自测: 全送达 / 部分丢失收敛 / 重复投递幂等 / 重启收敛 / 新数据不误伤。"""

import json
import os
import random
import tempfile
import unittest
from collections import Counter

from invalidation import (
    ArrayVersionTable,
    CacheNode,
    DictVersionTable,
    Message,
    MonotonicClock,
    Origin,
)


def expected_table(origins, size=None):
    """根据所有 origin 的日志, 计算每个 key 的理论最大失效版本 (最终一致基线)。"""
    if size is not None:
        exp = ArrayVersionTable(size)
        for o in origins:
            for m in o.log:
                exp.merge(m.key, m.version)
        return exp
    exp = DictVersionTable()
    for o in origins:
        for m in o.log:
            exp.merge(m.key, m.version)
    return exp


def nodes_converged(nodes, origins, size=None):
    """最终一致性校验: 序号连续 + 版本表与基线一致 + 无残留脏条目。"""
    exp = expected_table(origins, size).data
    result = {"converged": 0, "seq_mismatch": 0, "table_mismatch": 0,
              "stale_residue": 0, "checked": len(nodes)}
    for n in nodes:
        ok = True
        for o in origins:
            if n.contig.get(o.id, 0) != o.seq:
                result["seq_mismatch"] += 1
                ok = False
        if n.table.data != exp:
            result["table_mismatch"] += 1
            ok = False
        if n.stale_entries():
            result["stale_residue"] += 1
            ok = False
        if ok:
            result["converged"] += 1
    return result


class InvalidationTests(unittest.TestCase):
    def setUp(self):
        self.rng = random.Random(20261005)

    # ---------------------------------------------------------- 1) 全部送达
    def test_full_delivery(self):
        clock = MonotonicClock()
        origin = Origin("o1")
        nodes = [CacheNode(i) for i in range(5)]
        # 每个节点先写入 50 个 key 的旧缓存
        for n in nodes:
            for k in range(50):
                n.write(k, f"old-{k}", clock.tick())
        msgs = [origin.invalidate(k, clock.tick()) for k in range(50)]
        for n in nodes:
            n.receive_batch(msgs)
        chk = nodes_converged(nodes, [origin])
        print("\n[1 全部送达] 校验:", json.dumps(chk, ensure_ascii=False))
        self.assertEqual(chk["converged"], 5)
        for n in nodes:
            for k in range(50):
                self.assertIsNone(n.read(k))          # 旧缓存全部失效
            self.assertEqual(n.applied, 50)

    # ---------------------------------------------------------- 2) 部分丢失 -> 反熵收敛
    def test_partial_loss_converges(self):
        clock = MonotonicClock()
        origin = Origin("o1")
        nodes = [CacheNode(i) for i in range(8)]
        for n in nodes:
            for k in range(200):
                n.write(k, "old", clock.tick())
        stats = Counter()
        for i in range(1000):
            msg = origin.invalidate(self.rng.randrange(200), clock.tick())
            for n in nodes:
                r = self.rng.random()
                if r < 0.3:                       # 30% 广播丢失
                    stats["lost"] += 1
                else:
                    n.receive(msg)
        before = nodes_converged(nodes, [origin])
        # 反熵补齐 (模拟多轮心跳, 直到无人再拉到消息)
        rounds = 0
        while True:
            rounds += 1
            total = sum(n.anti_entropy([origin]) for n in nodes)
            if total == 0:
                break
        after = nodes_converged(nodes, [origin])
        stats["repaired"] = sum(n.repaired for n in nodes)
        stats["dup_buffer"] = sum(len(b) for n in nodes for b in n.buffered.values())
        print("\n[2 部分丢失] 补齐前:", json.dumps(before, ensure_ascii=False))
        print("[2 部分丢失] 补齐后:", json.dumps(after, ensure_ascii=False),
              "反熵轮数:", rounds, "统计:", dict(stats))
        self.assertLess(before["converged"], 8)    # 丢包确实造成过不一致
        self.assertEqual(after["converged"], 8)
        self.assertGreater(stats["repaired"], 0)

    # ---------------------------------------------------------- 3) 重复投递 -> 幂等
    def test_duplicate_delivery_idempotent(self):
        clock = MonotonicClock()
        origin = Origin("o1")
        n = CacheNode("dup")
        for k in range(100):
            n.write(k, "old", clock.tick())
        msgs = [origin.invalidate(k, clock.tick()) for k in range(100)]
        # 每条消息重复投递 1~3 次, 顺序也打乱
        for m in msgs:
            for _ in range(self.rng.randint(1, 3)):
                n.receive(m)
        self.assertEqual(n.applied, 100)
        self.assertGreater(n.dups_dropped, 0)
        # 反熵之后再次把整个历史重放一遍: 必须全部 no-op
        applied_before = n.applied
        for m in origin.fetch(1, origin.seq + 1):
            n.receive(m)
        self.assertEqual(n.applied, applied_before)
        chk = nodes_converged([n], [origin])
        print("\n[3 重复投递] 重复丢弃次数:", n.dups_dropped,
              " 重放后新增应用:", n.applied - applied_before,
              " 校验:", json.dumps(chk, ensure_ascii=False))
        self.assertEqual(chk["converged"], 1)

    # ---------------------------------------------------------- 4) 广播期间重启 -> 收敛
    def test_restart_during_broadcast(self):
        clock = MonotonicClock()
        origin = Origin("o1")
        alive = CacheNode("alive")
        victim = CacheNode("victim")
        for n in (alive, victim):
            for k in range(300):
                n.write(k, "old", clock.tick())
        tmpdir = tempfile.mkdtemp(prefix="cache-restart-")
        path = os.path.join(tmpdir, "victim.json")
        missed = 0
        for i in range(2000):
            msg = origin.invalidate(self.rng.randrange(300), clock.tick())
            alive.receive(msg)
            if i == 999:                        # 播到一半: 落盘 (检查点)
                victim.dump(path)
            if i >= 999:                        # 检查点之后宕机: 未落盘的全部丢失
                missed += 1
            else:
                victim.receive(msg)
        # 重启: 从磁盘恢复, 错过的消息由反熵补齐
        restarted = CacheNode.load(path)
        gap_at_restart = origin.seq - restarted.contig.get("o1", 0)
        pulled = restarted.anti_entropy([origin])
        chk_alive = nodes_converged([alive], [origin])
        chk_victim = nodes_converged([restarted], [origin])
        same_as_alive = restarted.table.data == alive.table.data
        print("\n[4 节点重启] 宕机期间错过:", missed,
              " 重启时缺口:", gap_at_restart, " 反熵拉回:", pulled)
        print("[4 节点重启] 重启节点校验:", json.dumps(chk_victim, ensure_ascii=False),
              " 与在线节点版本表一致:", same_as_alive,
              " 旧缓存残留:", len(restarted.stale_entries()))
        self.assertEqual(gap_at_restart, missed)
        self.assertEqual(pulled, missed)
        self.assertEqual(chk_victim["converged"], 1)
        self.assertTrue(same_as_alive)

    # ---------------------------------------------------------- 5) 不误伤广播后的新写入
    def test_new_write_not_clobbered(self):
        clock = MonotonicClock()
        origin = Origin("o1")
        n = CacheNode("new-write")
        key = "hotkey"
        n.write(key, "v-old", clock.tick())
        inv = origin.invalidate(key, clock.tick())      # version = 2 左右
        n.receive(inv)
        self.assertIsNone(n.read(key))                  # 旧数据失效

        # 广播之后的新写入, 版本号严格更大
        new_ver = clock.tick()
        self.assertTrue(n.write(key, "v-NEW", new_ver))

        # 旧失效延迟/重复再投递任意多次
        for _ in range(5):
            n.receive(inv)
        n.anti_entropy([origin])                        # 反熵重放历史
        for _ in range(5):
            n.receive(inv)

        value = n.read(key)
        data = {
            "invalidation_version": inv.version,
            "new_write_version": new_ver,
            "read_after_duplicate_replay": value,
            "dups_dropped": n.dups_dropped,
            "stale_write_rejected_when_old_version_rewrite": (
                not n.write(key, "v-STALE", inv.version - 1)
            ),
        }
        print("\n[5 新写保护] 验证数据:", json.dumps(data, ensure_ascii=False))
        self.assertEqual(value, "v-NEW")               # 新数据安然无恙
        self.assertEqual(n.stale_writes_rejected, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
