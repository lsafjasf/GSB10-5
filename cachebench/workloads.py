"""访问序列与缺失代价模型（合成模型，参数与负载含义见各函数文档）。

所有生成器都是惰性 generator：1M 次访问不会物化在内存里，
保证 benchmark 测到的内存峰值主要来自缓存策略本身。
相同 seed 下序列可复现，各策略面对的是完全相同的访问流。
"""

import bisect
import random


def gen_sequential(n, working_set):
    """顺序扫描：反复遍历 [0, working_set)。

    代表负载：全表扫描、备份、批处理报表、日志回放。
    特点：键在很长周期后才复用，容量 < 工作集时基于"最近性"的策略全部失效。
    """
    for i in range(n):
        yield i % working_set


def gen_zipf(n, working_set, s=1.1, seed=1):
    """Zipf 热点：第 k 热的键被访问概率正比于 1/k^s。

    代表负载：Web/CDN 对象请求、社交网络读（少数热键贡献大部分流量）。
    s=1.1 是 Web 代理/CDN 轨迹中常见的拟合值（文献多在 0.9~1.2）。
    """
    weights = [1.0 / (k + 1) ** s for k in range(working_set)]
    cdf = []
    acc = 0.0
    for w in weights:
        acc += w
        cdf.append(acc)
    total = acc
    rng = random.Random(seed)
    rand = rng.random
    for _ in range(n):
        yield bisect.bisect_left(cdf, rand() * total)


def gen_uniform(n, working_set, seed=2):
    """均匀随机：每个键等概率。

    代表负载：哈希分片后的主键点查（如按 user_id 随机查询）、
    无局部性的最坏情形。理论上任何在线策略命中率都趋近 capacity/working_set。
    """
    rng = random.Random(seed)
    rr = rng.randrange
    for _ in range(n):
        yield rr(working_set)


WORKLOADS = {
    "scan": gen_sequential,
    "zipf": gen_zipf,
    "uniform": gen_uniform,
}


def make_costs(working_set, seed=3):
    """每个键的缺失代价（回源成本），对数正态分布，中位数为 1。

    代表含义：不同对象的回源延迟/大小差异（如 1KB 配置项 vs 1MB 图片）。
    对数正态是对象大小分布的常见拟合。代价只与键绑定、与策略无关，
    因此各策略在同一负载下面对的代价序列完全一致。
    """
    rng = random.Random(seed)
    return [rng.lognormvariate(0.0, 1.0) for _ in range(working_set)]
