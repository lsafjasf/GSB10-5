"""限流规则批量下发自测：python3 -m unittest -v test_rollout"""
import unittest

from rate_limit_rollout import (
    Node, NodeState, Phase, RolloutManager, Rule, validate_no_amplification,
)

V1 = Rule(version=1, limit=100.0, burst=10)
V2_UP = Rule(version=2, limit=200.0, burst=10)   # 放量上调（危险方向）
V2_DOWN = Rule(version=2, limit=60.0, burst=10)  # 放量下调
V3 = Rule(version=3, limit=300.0, burst=10)
V4 = Rule(version=4, limit=400.0, burst=10)


def make_nodes(n, rule=V1):
    return [Node(f"node-{i:02d}", rule) for i in range(n)]


class RolloutTests(unittest.TestCase):

    def test_all_success(self):
        nodes = make_nodes(6)
        mgr = RolloutManager(nodes)
        result = mgr.rollout(V2_UP)

        self.assertEqual(result.status, "COMMITTED")
        self.assertIsNone(result.rollback_ms)
        self.assertGreater(result.duration_ms, 0.0)
        # 最终全部节点活动规则为 v2、状态 STEADY、无暂存残留
        self.assertTrue(all(n.active == V2_UP and n.state is NodeState.STEADY
                            and n.staged is None for n in nodes))
        # 阶段版本直方图
        by_stage = {s["stage"]: s["versions"] for s in result.trace}
        self.assertEqual(by_stage["BEFORE"], {1: 6})
        self.assertEqual(by_stage["AFTER_PREPARE"], {1: 6})       # 暂存但不生效
        self.assertEqual(by_stage["AFTER_COMMIT"], {1: 6})        # 过渡期仍执行旧版本号
        self.assertEqual(by_stage["AFTER_ACTIVATE"], {2: 6})      # 统一激活
        # 状态推进
        self.assertEqual(result.trace[2]["states"]["TRANSITION"], 6)
        # 全程无放量失控
        violations = validate_no_amplification(result.trace, 100.0, 200.0)
        self.assertEqual(violations, [])

    def test_transition_clamps_to_min_for_increase(self):
        """上调场景：过渡期每个节点只能放到 min(100,200)=100，不能提前按 200 放量。"""
        nodes = make_nodes(4)
        mgr = RolloutManager(nodes)
        mgr.rollout(V2_UP)
        commit_snap = next(s for s in mgr.trace if s["stage"] == "AFTER_COMMIT")
        self.assertEqual(commit_snap["total_limit"], 400.0)  # 而非 800
        for nid, (state, ver, eff) in commit_snap["nodes"].items():
            self.assertEqual(state, "TRANSITION")
            self.assertEqual(eff, 100.0)

    def test_decrease_also_safe(self):
        nodes = make_nodes(4)
        result = RolloutManager(nodes).rollout(V2_DOWN)
        self.assertEqual(result.status, "COMMITTED")
        # 下调时过渡期取 min(100,60)=60，同样不会出现新旧并存失控
        commit_snap = next(s for s in result.trace if s["stage"] == "AFTER_COMMIT")
        self.assertEqual(commit_snap["total_limit"], 240.0)
        self.assertEqual(validate_no_amplification(result.trace, 100.0, 60.0), [])

    def test_prepare_failure_rolls_back_uniform(self):
        nodes = make_nodes(6)
        nodes[3].fail_on = {Phase.PREPARE}  # node-03 在预置阶段失败
        result = RolloutManager(nodes).rollout(V2_UP)

        self.assertEqual(result.status, "ROLLED_BACK")
        self.assertEqual(result.failed_phase, "prepare")
        self.assertEqual(result.failed_nodes, ["node-03"])
        self.assertIsNotNone(result.rollback_ms)
        self.assertGreaterEqual(result.rollback_ms, 0.0)
        # 回退后全部节点统一在旧规则 v1
        self.assertTrue(all(n.active.version == 1 and n.state is NodeState.STEADY
                            and n.staged is None for n in nodes))
        audit = RolloutManager(nodes).audit()
        self.assertFalse(audit["skew"])
        self.assertEqual(audit["versions"], {1: 6})
        self.assertEqual(audit["dirty_nodes"], [])
        # 回退后还可以重新下发成功
        for n in nodes:
            n.fail_on.clear()
        retry = RolloutManager(nodes).rollout(V2_UP)
        self.assertEqual(retry.status, "COMMITTED")

    def test_commit_failure_rolls_back_uniform(self):
        nodes = make_nodes(6)
        nodes[5].fail_on = {Phase.COMMIT}  # node-05 在过渡确认阶段失败
        result = RolloutManager(nodes).rollout(V2_UP)

        self.assertEqual(result.status, "ROLLED_BACK")
        self.assertEqual(result.failed_phase, "commit")
        self.assertIn("node-05", result.failed_nodes)
        # 即使部分节点已进入过渡期，回退后也统一恢复 v1
        self.assertTrue(all(n.active.version == 1 and n.state is NodeState.STEADY
                            and n.staged is None for n in nodes))
        abort_snap = next(s for s in result.trace if s["stage"] == "AFTER_ABORT")
        self.assertEqual(abort_snap["versions"], {1: 6})
        self.assertEqual(abort_snap["total_limit"], 600.0)

    def test_new_rule_during_rollout_is_rejected_deterministically(self):
        """下发中途收到新规则：确定性拒绝，进行中的下发照常完成。"""
        nodes = make_nodes(6)
        mgr = RolloutManager(nodes)

        seen = []

        def hook(node, phase):
            if phase is Phase.PREPARE and node.node_id == "node-02":
                # 中途插入一次 v4 下发
                seen.append(mgr.rollout(V4))

        nodes[2].on_phase = hook
        result = mgr.rollout(V3)

        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].status, "REJECTED_IN_PROGRESS")
        self.assertEqual(seen[0].to_version, 4)
        # 进行中的 v3 下发不受影响
        self.assertEqual(result.status, "COMMITTED")
        self.assertTrue(all(n.active.version == 3 for n in nodes))
        # 拒绝不改变锁状态：下发结束后新规则可以正常下发（确定性）
        after = mgr.rollout(V4)
        self.assertEqual(after.status, "COMMITTED")
        self.assertTrue(all(n.active.version == 4 for n in nodes))

    def test_version_chaos_reconciled(self):
        """节点版本混乱（部分 v1、部分 v2，且有暂存残留）：审计发现并收敛。"""
        nodes = make_nodes(6)
        nodes[4].active = V2_UP
        nodes[4].prepare(V3)               # 残留暂存，状态 STAGED
        nodes[5].active = V2_UP
        nodes[5].prepare(V3)
        nodes[5].commit()                  # 残留过渡，状态 TRANSITION

        mgr = RolloutManager(nodes)
        audit = mgr.audit()
        self.assertTrue(audit["skew"])
        self.assertEqual(audit["versions"], {1: 4, 2: 2})
        self.assertEqual(sorted(audit["dirty_nodes"]), ["node-04", "node-05"])

        result = mgr.reconcile(V3)
        self.assertEqual(result.status, "COMMITTED")
        audit2 = mgr.audit()
        self.assertFalse(audit2["skew"])
        self.assertEqual(audit2["versions"], {3: 6})
        self.assertEqual(audit2["dirty_nodes"], [])
        self.assertTrue(all(n.state is NodeState.STEADY and n.staged is None
                            and n.active == V3 for n in nodes))

    def test_idempotent_reconcile_when_already_uniform(self):
        nodes = make_nodes(3)
        mgr = RolloutManager(nodes)
        result = mgr.reconcile(V1)  # 已是 v1，再跑一遍
        self.assertEqual(result.status, "COMMITTED")
        self.assertTrue(all(n.active == V1 for n in nodes))


if __name__ == "__main__":
    unittest.main(verbosity=2)
