"""10 万发布单压测：检测耗时与内存峰值。

运行：python3 benchmark.py [--n 100000] [--seed 20261006]
"""

import argparse
import platform
import random
import resource as _resource
import time
import tracemalloc

from release_conflict import Release, Resource, detect_conflicts, schedule


def build_workload(n, seed):
    """构造确定性工作负载：
    - 500 个独占资源（capacity=1）+ 20 个共享资源池（capacity=2，产生传递性冲突）；
    - 每个发布单占用 1~3 个资源，约 5% 会占用共享池；
    - 窗口均匀分布在 30 天内，时长 30~180 分钟。
    """
    rng = random.Random(seed)
    n_excl, n_pool = 500, 20
    resources = {f"svc-{i}": Resource(f"svc-{i}", 1) for i in range(n_excl)}
    resources.update({f"pool-{i}": Resource(f"pool-{i}", 2) for i in range(n_pool)})

    horizon = 30 * 24 * 60  # 30 天，分钟粒度
    releases = []
    for i in range(n):
        start = rng.randrange(0, horizon - 180)
        dur = rng.randrange(30, 180)
        k = rng.choice((1, 1, 1, 2, 2, 3))
        demands = {}
        if rng.random() < 0.05:
            demands[f"pool-{rng.randrange(n_pool)}"] = 1
        while len(demands) < k:
            demands[f"svc-{rng.randrange(n_excl)}"] = 1
        releases.append(Release(f"rel-{i}", start, start + dur, demands))
    return resources, releases


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=100_000)
    parser.add_argument("--seed", type=int, default=20261006)
    args = parser.parse_args()

    print(f"Python {platform.python_version()} | {platform.platform()}")
    print(f"工作负载: n={args.n} 发布单, seed={args.seed}")

    t0 = time.perf_counter()
    resources, releases = build_workload(args.n, args.seed)
    t1 = time.perf_counter()
    n_res = len(resources)
    n_demands = sum(len(r.resource_demands) for r in releases)
    print(f"数据生成: {t1 - t0:.3f}s | 资源 {n_res} 个, 资源占用记录 {n_demands} 条")

    tracemalloc.start()
    t2 = time.perf_counter()
    conflicts = detect_conflicts(releases, resources)
    t3 = time.perf_counter()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    pairs = sum(1 for c in conflicts if c.size == 2)
    triples = sum(1 for c in conflicts if c.size == 3)
    bigger = sum(1 for c in conflicts if c.size > 3)
    print(f"冲突检测: {t3 - t2:.3f}s")
    print(f"冲突清单: 共 {len(conflicts)} 条 (两元 {pairs}, 三元传递性 {triples}, 更大 {bigger})")
    print(f"tracemalloc 内存峰值: {peak / 1024 / 1024:.1f} MiB")

    t4 = time.perf_counter()
    plan = schedule(releases, resources)
    t5 = time.perf_counter()
    moved = sum(1 for s in plan if s.start != s.release.start)
    print(f"排期({args.n} 单): {t5 - t4:.3f}s | 其中 {moved} 单被顺延")

    executed = [
        Release(s.release.id, s.start, s.end, dict(s.release.resource_demands))
        for s in plan
    ]
    recheck = detect_conflicts(executed, resources)
    print(f"排期结果复检: {'通过（无冲突）' if not recheck else f'失败（{len(recheck)} 条冲突）'}")

    peak_rss = _resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss
    print(f"进程 RSS 峰值(含解释器与全部数据): {peak_rss / 1024:.1f} MiB")


if __name__ == "__main__":
    main()
