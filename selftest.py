# -*- coding: utf-8 -*-
"""限流规则批量下发：自测 + 性能基准（仅标准库，直接运行：python3 selftest.py）。"""
from __future__ import annotations

import random
import time
import tracemalloc

from rollout import (
    ClusterVersionDrift,
    Coordinator,
    Node,
    RolloutInProgress,
    Rule,
)

PASS = "PASS"
FAILED = []


def check(cond: bool, msg: str) -> None:
    assert cond, msg


def print_snapshot_table(title: str, coordinator: Coordinator) -> None:
    print("    %-28s %-8s %-12s %-10s %-8s" %
          ("阶段/节点", "生效", "本地持有", "护栏旧版", "执行阈值"))
    for s in coordinator.snapshots():
        print("    %-28s v%-7d %-12s %-10s %-8d" %
              (s["node"], s["active"], "v" + "/v".join(map(str, s["staged"])),
               ("v%d" % s["guard_from"]) if s["guard_from"] is not None else "-",
               s["effective_limit"]))


def make_cluster(n: int, limit: int = 100) -> Coordinator:
    return Coordinator([Node("node-%03d" % i, Rule(1, limit)) for i in range(n)])


# ---------------------------------------------------------------- 场景 1
def test_all_success():
    print("\n[1] 全部成功：5 节点，v1(100) -> v2(500)，输出各阶段节点版本数据")
    coord = make_cluster(5)
    print("  -- PREPARE 完成、BARRIER（新规则已暂存但未生效） --")
    # 手工驱动以便展示 PREPARE 阶段快照
    new_rule = Rule(2, 500)
    for n in coord.nodes:
        n.stage(new_rule)
    print_snapshot_table("PREPARE", coord)
    check(all(n.active_version == 1 for n in coord.nodes), "PREPARE 后必须仍全部生效 v1")
    check(coord.cluster_capacity() == 500, "PREPARE 期间放量必须仍是旧规则总量 500")

    print("  -- COMMIT 中途（2/5 已激活，护栏窗口） --")
    for n in coord.nodes[:2]:
        n.activate(2)
    print_snapshot_table("COMMIT 2/5", coord)
    caps_mid = coord.cluster_capacity()
    check(caps_mid == 500, "护栏下中途放量必须 = 旧总量 500，实际 %d" % caps_mid)

    for n in coord.nodes[2:]:
        n.activate(2)
    print("  -- SEAL 完成 --")
    for n in coord.nodes:
        n.seal()
    print_snapshot_table("SEAL", coord)
    check(coord.audit().keys() == {2}, "SEAL 后必须全部生效 v2")
    check(all(n.staged.keys() == {2} for n in coord.nodes), "SEAL 后旧规则必须被清理")
    check(coord.cluster_capacity() == 2500, "SEAL 后必须按新规则放量 2500")
    print("  => %s 放量：PREPARE=500 COMMIT中途=500 SEAL后=2500" % PASS)


# ---------------------------------------------------------------- 场景 2
def test_no_mixed_amplification():
    print("\n[2] 放量校验：100 节点 v1(100)->v2(500)，新旧并存期护栏 vs 无护栏")
    coord = make_cluster(100)
    old_limit, new_limit, n = 100, 500, 100
    guarded_caps, naive_caps = [], []

    def hook(done, total):
        guarded_caps.append(coord.cluster_capacity())
        # 无护栏（直接切新阈值）时的理论放量，用于对照
        naive_caps.append(done * new_limit + (total - done) * old_limit)

    report = coord.rollout(Rule(2, new_limit), on_commit_progress=hook)
    old_total = n * old_limit
    max_guarded, max_naive = max(guarded_caps), max(naive_caps)
    print("  旧规则集群总放量: %d ; 新规则集群总放量: %d" % (old_total, n * new_limit))
    print("  COMMIT 窗口逐节点放量(护栏)  : min=%d max=%d" % (min(guarded_caps), max_guarded))
    print("  COMMIT 窗口逐节点放量(无护栏): min=%d max=%d  <- 中途会冲到 %d，超发 %d" %
          (min(naive_caps), max_naive, max_naive, max_naive - old_total))
    check(report.success, "下发必须成功")
    check(max_guarded <= old_total, "护栏放量在任何时刻都不能超过旧规则总量")
    print("  => %s 新旧并存期总放量恒为 %d（<=旧总量 %d），无超发" % (PASS, max_guarded, old_total))

    # 收紧方向也不超发：v2(500)->v3(200)，已切换节点立刻变严
    coord2 = make_cluster(100, limit=500)
    caps = []
    r2 = coord2.rollout(Rule(3, 200), on_commit_progress=lambda d, t: caps.append(coord2.cluster_capacity()))
    print("  收紧场景 v2(500)->v3(200)：COMMIT 窗口放量 min=%d max=%d（单调下降，SEAL 后 %d）" %
          (min(caps), max(caps), coord2.cluster_capacity()))
    check(r2.success and max(caps) <= 50000 and coord2.cluster_capacity() == 20000, "收紧方向校验失败")


# ---------------------------------------------------------------- 场景 3
def test_prepare_failure():
    print("\n[3] PREPARE 中途失败：node-003 stage v2 失败 -> 回退")
    coord = make_cluster(5)
    coord.nodes[3].fail_stage_on = {2}
    report = coord.rollout(Rule(2, 500))
    check(not report.success and report.rolled_back, "必须回退")
    check(coord.audit().keys() == {1}, "回退后必须统一在 v1")
    check(all(2 not in n.staged for n in coord.nodes), "所有节点不得残留 v2")
    print("  结果: %s ; 回退后版本分布: %s ; v2 残留: 无 ; 回退用时 %.3f ms" %
          ("已回退", {1: 5}, report.rollback_ms))
    print("  => %s" % PASS)


# ---------------------------------------------------------------- 场景 4
def test_commit_failure():
    print("\n[4] COMMIT 中途失败：node-003 activate v2 失败 -> 已激活节点回退")
    coord = make_cluster(5)
    coord.nodes[3].fail_activate_on = {2}
    caps_during = []

    def hook(done, total):
        caps_during.append(coord.cluster_capacity())

    report = coord.rollout(Rule(2, 500), on_commit_progress=hook)
    check(not report.success and report.rolled_back, "必须回退")
    dist = coord.audit()
    check(dist.keys() == {1} and len(dist[1]) == 5, "回退后 5 个节点必须统一在 v1")
    check(all(2 not in n.staged for n in coord.nodes), "所有节点不得残留 v2")
    check(all(n.guard_from is None for n in coord.nodes), "护栏必须全部解除")
    check(max(caps_during) <= 500, "回退前的窗口也不得超发")
    check(coord.cluster_capacity() == 500, "回退后放量恢复旧规则 500")
    print("  失败点: 3/5 已激活后 node-003 报错；3 个已激活节点 revert v1，全部 discard v2")
    print("  结果: %s ; 回退后版本分布: {v1: 5} ; v2 残留: 无 ; 护栏: 已解除" % "已回退")
    print("  下发+回退总用时 %.3f ms，其中回退用时 %.3f ms" % (report.duration_ms, report.rollback_ms))
    print("  => %s" % PASS)


# ---------------------------------------------------------------- 场景 5
def test_concurrent_change():
    print("\n[5] 下发期间又收到新规则：COMMIT 窗口内提交 v3")
    coord = make_cluster(5)
    outcomes = []

    def hook(done, total):
        if done == 3:
            try:
                coord.rollout(Rule(3, 900))
                outcomes.append("accepted")
            except RolloutInProgress as exc:
                outcomes.append("rejected:%s" % exc)

    report = coord.rollout(Rule(2, 500), on_commit_progress=hook)
    check(outcomes == ["rejected:rollout of v2 in progress"], "v3 必须被确定性拒绝，实际 %s" % outcomes)
    check(report.success and coord.audit().keys() == {2}, "v2 必须不受影响地完成")
    check(all(3 not in n.staged for n in coord.nodes), "任何节点上都不得出现 v3 暂存")
    # v2 完成后 v3 可正常下发（拒绝只针对进行中的窗口）
    report3 = coord.rollout(Rule(3, 900))
    check(report3.success and coord.audit().keys() == {3}, "窗口结束后 v3 必须可正常下发")
    print("  处理策略: 单飞锁 + 确定性拒绝（调用方收到 RolloutInProgress，可稍后重试）")
    print("  结果: v3 在窗口内被拒绝、无节点暂存；v2 正常 SEAL；窗口结束后 v3 下发成功")
    print("  => %s" % PASS)


# ---------------------------------------------------------------- 场景 6
def test_version_chaos():
    print("\n[6] 节点版本混乱：人为制造 v1/v2/v3 漂移 -> 审计 -> 拒绝下发 -> 修复对齐")
    coord = make_cluster(6)
    rules = {1: Rule(1, 100), 2: Rule(2, 300), 3: Rule(3, 700)}
    random.seed(7)
    forced = [1, 2, 2, 3, 1, 3]
    for node, ver in zip(coord.nodes, forced):
        for v in range(1, ver + 1):
            node.stage(rules[v])
        node.activate(ver)
        node.seal()
    before = {("v%d" % k): len(v) for k, v in coord.audit().items()}
    print("  修复前版本分布: %s" % before)

    try:
        coord.rollout(Rule(4, 800))
        check(False, "版本混乱时必须拒绝下发")
    except ClusterVersionDrift as exc:
        print("  rollout(v4) 被拒绝: ClusterVersionDrift(%s)" %
              {("v%d" % k): len(v) for k, v in exc.args[0].items()})

    coord.repair(1)
    after = {("v%d" % k): len(v) for k, v in coord.audit().items()}
    check(coord.audit().keys() == {1}, "repair 后必须统一到 v1")
    print("  repair(v1) 后版本分布: %s" % after)
    report = coord.rollout(Rule(4, 800))
    check(report.success and coord.audit().keys() == {4}, "对齐后 v4 必须下发成功")
    print("  对齐后 rollout(v4) 成功，最终版本分布: {v4: 6}")
    print("  => %s" % PASS)


# ---------------------------------------------------------------- 基准
def benchmark():
    print("\n[7] 性能基准：1000 节点 × 100 次下发（全部成功路径）")
    coord = make_cluster(1000)
    tracemalloc.start()
    t0 = time.perf_counter()
    for i in range(2, 102):
        coord.rollout(Rule(i, 100 + i))
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    check(coord.audit().keys() == {101}, "基准结束版本必须为 v101")
    print("  100 次下发总耗时: %.1f ms ; 平均单次: %.3f ms" % (elapsed_ms, elapsed_ms / 100))
    print("  Python 对象内存峰值(tracemalloc): %.1f KiB (%.2f MiB)" % (peak / 1024, peak / 1024 / 1024))

    print("\n  回退耗时基准：1000 节点，COMMIT 在第 700 个节点失败")
    coord2 = make_cluster(1000)
    coord2.nodes[699].fail_activate_on = {2}
    report = coord2.rollout(Rule(2, 500))
    check(report.rolled_back and coord2.audit().keys() == {1}, "基准回退必须成功")
    print("  下发+回退总用时 %.3f ms，其中 700 节点 revert + 1000 节点 discard 的回退用时 %.3f ms" %
          (report.duration_ms, report.rollback_ms))


def main():
    tests = [test_all_success, test_no_mixed_amplification, test_prepare_failure,
             test_commit_failure, test_concurrent_change, test_version_chaos, benchmark]
    for t in tests:
        t()
    print("\n全部场景通过：%d/%d" % (len(tests), len(tests)))


if __name__ == "__main__":
    main()
