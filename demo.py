"""演示：阶段数据、放量校验、回退、并发新规则。python3 demo.py"""
from rate_limit_rollout import (
    Node, Phase, RolloutManager, Rule, validate_no_amplification,
)

V1 = Rule(1, 100.0, 10)
V2 = Rule(2, 200.0, 10)   # 上调：最危险的放量方向
V3 = Rule(3, 300.0, 10)
V4 = Rule(4, 400.0, 10)


def show_trace(trace, title):
    print(f"\n== {title} ==")
    print(f"{'阶段':<16}{'版本分布':<14}{'状态分布':<28}{'全局限流总量':>10}")
    for s in trace:
        ver = ",".join(f"v{k}x{c}" for k, c in sorted(s["versions"].items()))
        st = ",".join(f"{k}x{c}" for k, c in sorted(s["states"].items()))
        print(f"{s['stage']:<16}{ver:<14}{st:<28}{s['total_limit']:>10.0f}")


def show_nodes(snap, stage):
    s = next(x for x in snap if x["stage"] == stage)
    print(f"  {stage} 逐节点(状态,版本,实际限流): "
          + " ".join(f"{nid.split('-')[1]}=({st[:5]},v{ver},{eff:.0f})"
                     for nid, (st, ver, eff) in sorted(s["nodes"].items())))


print("=" * 72)
print("场景 1：全部成功（v1 限流100 -> v2 限流200，6 节点）")
print("=" * 72)
nodes = [Node(f"node-{i}", V1) for i in range(6)]
mgr = RolloutManager(nodes)
r = mgr.rollout(V2)
show_trace(r.trace, "各阶段版本数据")
show_nodes(r.trace, "AFTER_PREPARE")
show_nodes(r.trace, "AFTER_COMMIT")
show_nodes(r.trace, "AFTER_ACTIVATE")
viol = validate_no_amplification(r.trace, 100.0, 200.0)
print(f"\n放量校验: {'PASS 全程无失控' if not viol else 'FAIL'}")
print(f"  安全上限 = max(旧600, 新1200) = 1200；过渡期单节点上限 = min(100,200) = 100")
for s in r.trace:
    print(f"  [{s['stage']:<16}] 总量 {s['total_limit']:>6.0f} <= 1200  OK")
print(f"下发结果: {r.status}, 耗时 {r.duration_ms:.3f} ms")

print("\n" + "=" * 72)
print("场景 2：中途失败回退（node-3 在 commit 阶段失败）")
print("=" * 72)
nodes = [Node(f"node-{i}", V1) for i in range(6)]
nodes[3].fail_on = {Phase.COMMIT}
mgr = RolloutManager(nodes)
r = mgr.rollout(V2)
show_trace(r.trace, "失败与回退各阶段版本数据")
print(f"\n回退结果: {r.status}, 失败阶段={r.failed_phase}, 失败节点={r.failed_nodes}")
print(f"回退用时: {r.rollback_ms:.3f} ms, 本次下发总耗时: {r.duration_ms:.3f} ms")
a = mgr.audit()
print(f"回退后审计: skew={a['skew']}, 版本={a['versions']}, 残留节点={a['dirty_nodes']}")

print("\n" + "=" * 72)
print("场景 3：下发进行中又收到新规则 v4")
print("=" * 72)
nodes = [Node(f"node-{i}", V1) for i in range(6)]
mgr = RolloutManager(nodes)
box = {}
def hook(node, phase):
    if phase is Phase.PREPARE and node.node_id == "node-2":
        box["rejected"] = mgr.rollout(V4)
nodes[2].on_phase = hook
r = mgr.rollout(V3)
print(f"进行中的 v3 下发: {r.status}")
print(f"中途插入的 v4 下发: {box['rejected'].status} (确定性拒绝, 互斥锁保证)")
print(f"最终版本分布: {mgr.audit()['versions']}")
r2 = mgr.rollout(V4)
print(f"v3 下发结束后再发 v4: {r2.status}, 最终版本分布: {mgr.audit()['versions']}")

print("\n" + "=" * 72)
print("场景 4：节点版本混乱 -> 审计 -> 收敛")
print("=" * 72)
nodes = [Node(f"node-{i}", V1) for i in range(6)]
nodes[4].active = V2
nodes[4].prepare(V3)
nodes[5].active = V2
nodes[5].prepare(V3)
nodes[5].commit()
mgr = RolloutManager(nodes)
a = mgr.audit()
print(f"修复前审计: skew={a['skew']}, 版本={a['versions']}, 残留节点={a['dirty_nodes']}")
r = mgr.reconcile(V3)
a = mgr.audit()
print(f"收敛下发: {r.status}, 耗时 {r.duration_ms:.3f} ms")
print(f"修复后审计: skew={a['skew']}, 版本={a['versions']}, 残留节点={a['dirty_nodes']}")
