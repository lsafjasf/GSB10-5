"""生成合成慢查询日志（每行一个耗时，单位 ms），并输出全量精确统计量。

混合分布（模拟真实慢查询场景）：
  - 98.9%  正常查询   lognormal(mu=1.6, sigma=0.9)   中位数约 5ms
  - 1.0%   偏慢查询   lognormal(mu=4.0, sigma=0.8)   中位数约 55ms
  - 0.1%   极端长尾   scale * Pareto(alpha)          秒级到分钟级

用法: python3 datagen.py <输出文件> <记录数> [--extreme] [--seed N]
精确统计量写入 <输出文件>.stats.json，供 report.py 对比使用。
"""
import json
import math
import sys
import time
from array import array
from random import Random

QS = (0.5, 0.9, 0.99, 0.999)


def exact_stats(vals) -> dict:
    ordered = sorted(vals)
    n = len(ordered)

    def q(p):
        return ordered[min(n - 1, max(0, math.ceil(p * n) - 1))]

    total = math.fsum(ordered)
    stats = {
        "n": n,
        "mean": total / n,
        "max": ordered[-1],
        "quantiles": {str(p): q(p) for p in QS},
        "tail_share": {},
    }
    for p in (0.99,):
        idx = min(n - 1, max(0, math.ceil(p * n) - 1))
        stats["tail_share"][str(p)] = math.fsum(ordered[idx:]) / total
    return stats


def generate(path: str, n: int, seed: int = 42, extreme: bool = False) -> dict:
    rng = Random(seed)
    alpha = 1.2 if extreme else 1.5
    scale = 1000.0 if extreme else 300.0
    vals = array("f")
    t0 = time.perf_counter()
    with open(path, "w") as f:
        for _ in range(n):
            u = rng.random()
            if u < 0.989:
                d = rng.lognormvariate(1.6, 0.9)
            elif u < 0.999:
                d = rng.lognormvariate(4.0, 0.8)
            else:
                d = scale * rng.paretovariate(alpha)
            f.write(f"{d:.3f}\n")
            vals.append(d)
    stats = exact_stats(vals)
    stats["extreme"] = extreme
    stats["seed"] = seed
    stats["gen_seconds"] = round(time.perf_counter() - t0, 2)
    with open(path + ".stats.json", "w") as f:
        json.dump(stats, f, indent=1)
    return stats


if __name__ == "__main__":
    path = sys.argv[1]
    n = int(sys.argv[2])
    extreme = "--extreme" in sys.argv
    seed = 42
    for i, a in enumerate(sys.argv):
        if a == "--seed":
            seed = int(sys.argv[i + 1])
    st = generate(path, n, seed=seed, extreme=extreme)
    print(f"生成 {st['n']:,} 条 -> {path}  耗时 {st['gen_seconds']}s  "
          f"mean={st['mean']:.3f}ms p99={st['quantiles']['0.99']:.1f}ms "
          f"p99.9={st['quantiles']['0.999']:.1f}ms max={st['max']:.1f}ms")
