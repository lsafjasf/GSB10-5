"""压测：10 万个节点、5 层嵌套，输出计算耗时与内存峰值。"""
import random
import resource
import time
import tracemalloc

from rlimit import DIMENSIONS, Node, Policy, resolve_tree

TOTAL, MAX_DEPTH = 100_000, 5


def build_tree(total=TOTAL, max_depth=MAX_DEPTH, seed=42):
    """构建恰好 total 个节点、max_depth 层的树（末层在同级父节点间均分）。"""
    rnd = random.Random(seed)
    root = Node("root", {d: v for d, v in
                         zip(DIMENSIONS, (64, 1 << 20, 65536, 1 << 20, 100_000))})
    # 均衡分支：1, b, b^2, b^3, 其余全部放在第 5 层
    b = int(round(total ** (1 / (max_depth - 1))))
    level, count, depth = [root], 1, 1
    while depth < max_depth:
        if depth == max_depth - 1:
            width = None  # 末层：把剩余名额轮询分给所有父节点
        else:
            width = b
        next_level = []
        quota = total - count
        for i, parent in enumerate(level):
            if width is None:  # 末层精确均分（前几个父节点多一个孩子）
                n_children = quota // len(level) + (1 if i < quota % len(level) else 0)
            else:
                n_children = width
            for _ in range(n_children):
                limits = {}
                for dim in DIMENSIONS:
                    roll = rnd.random()
                    if roll < 0.25:      # 25% 显式调小（含 0，验证零值）
                        limits[dim] = rnd.randint(0, 8)
                    elif roll < 0.30:    # 5% 试图突破父级 -> 触发收敛
                        limits[dim] = 10 ** 9
                    # 其余缺失 -> 继承
                next_level.append(parent.add(Node(f"n{count}", limits)))
                count += 1
        level, depth = next_level, depth + 1
    return root, count, depth


def stats(result):
    clamped = sum(1 for effs in result.values() for e in effs.values() if e.clamped)
    unlimited = sum(1 for effs in result.values()
                    for e in effs.values() if e.value is None)
    zeros = sum(1 for effs in result.values()
                for e in effs.values() if e.value == 0)
    return clamped, unlimited, zeros


def main():
    t0 = time.perf_counter()
    root, count, depth = build_tree()
    t1 = time.perf_counter()
    probe = resolve_tree(root)
    max_depth = max(eff.chain[-1].depth for effs in probe.values() for eff in effs.values())
    print(f"建树: {count} 节点, {max_depth + 1} 层（L0~L{max_depth}）, 建树耗时 {t1 - t0:.3f}s")

    # 计时：不挂 tracemalloc，避免内存追踪拖慢分配
    t2 = time.perf_counter()
    result = resolve_tree(root, Policy.CLAMP)
    t3 = time.perf_counter()
    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

    # 内存：单独跑一遍，tracemalloc 只测解析阶段的新增分配
    tracemalloc.start()
    result2 = resolve_tree(root, Policy.CLAMP)
    _, traced_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    clamped, unlimited, zeros = stats(result)
    print(f"解析: {len(result)} 节点 x {len(DIMENSIONS)} 维度, 耗时 {t3 - t2:.3f}s")
    print(f"进程峰值 RSS(含建树+结果, ru_maxrss): {peak_rss:.1f} MiB")
    print(f"解析阶段内存峰值(tracemalloc): {traced_peak / 1024 ** 2:.1f} MiB")
    print(f"校验: 收敛 {clamped} 处, 不限 {unlimited} 处, 生效为 0 共 {zeros} 处")


if __name__ == "__main__":
    main()
