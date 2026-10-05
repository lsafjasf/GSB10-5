"""压测：1000 节点 x 100 次下发，输出耗时与内存峰值。python3 bench.py"""
import resource
import time
import tracemalloc

from rate_limit_rollout import Node, RolloutManager, Rule

NODES, ROUNDS = 1000, 100

# 追踪从建池开始，内存峰值包含全部节点与下发器
tracemalloc.start()
nodes = [Node(f"node-{i}", Rule(1, 100.0, 10)) for i in range(NODES)]
# trace_nodes=False：压测只记录版本直方图，避免逐节点追踪数据计入峰值
mgr = RolloutManager(nodes, trace_nodes=False)
t0 = time.perf_counter()
durations = []
for i in range(ROUNDS):
    limit = 200.0 if i % 2 == 0 else 100.0
    r = mgr.rollout(Rule(i + 2, limit, 10))
    assert r.status == "COMMITTED", r.status
    durations.append(r.duration_ms)
total_s = time.perf_counter() - t0
cur, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()

audit = mgr.audit()
assert not audit["skew"] and not audit["dirty_nodes"]

print(f"节点数:            {NODES}")
print(f"下发次数:          {ROUNDS} (全部 COMMITTED, 最终版本 v{ROUNDS + 1})")
print(f"总耗时:            {total_s * 1000:.1f} ms")
print(f"单次下发平均耗时:  {sum(durations) / len(durations):.3f} ms")
print(f"单次下发最小/最大: {min(durations):.3f} / {max(durations):.3f} ms")
print(f"tracemalloc 峰值:  {peak / 1024 / 1024:.2f} MiB")
print(f"进程 RSS 峰值:     {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.2f} MiB")
