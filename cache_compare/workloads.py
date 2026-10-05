"""访问序列生成器。

来源说明：
- 本文件提供 4 种**带参数的合成模型**，每种对应一类典型真实负载，
  参数（工作集大小、偏斜度、扫描比例、随机种子）全部显式可控、可复现。
- 同时提供 load_trace() 读取真实轨迹文件（每行一个 key，或 CSV 首列），
  可直接接入公开轨迹，如 Wikipedia CDN / S3-FIFO 论文开源的缓存轨迹。

所有生成器均为惰性迭代器：100 万次访问不会物化在内存中，
保证内存测量只反映策略本身的开销。
"""

import bisect
import random


def scan(n_keys, total, rng):
    """顺序扫描：循环遍历 0..n_keys-1。

    代表负载：全表扫描、批处理、日志回放。
    特点：键只被访问一次就换下一批，是经典的"缓存杀手"，
    LRU/FIFO 在容量 < 工作集时命中率趋近 0。
    """
    for i in range(total):
        yield i % n_keys


def uniform(n_keys, total, rng):
    """均匀随机：每个键等概率。

    代表负载：请求被哈希打散后的无局部性场景（如随机 ID 查询）。
    理论命中率上限 ≈ 容量 / 工作集，与策略无关，用作对照基线。
    """
    randrange = rng.randrange
    for _ in range(total):
        yield randrange(n_keys)


def zipf(n_keys, total, rng, skew=1.2):
    """Zipf 热点：第 i 热的键概率正比于 1/i^skew。

    代表负载：热点集中场景——Web 缓存、CDN、键值存储。
    大量实测（如 Wikipedia 访问轨迹）表明流行度近似 Zipf 分布，
    skew 越大热点越集中。
    """
    cdf = []
    acc = 0.0
    for i in range(1, n_keys + 1):
        acc += 1.0 / (i ** skew)
        cdf.append(acc)
    random_ = rng.random
    bisect_ = bisect.bisect_left
    for _ in range(total):
        yield bisect_(cdf, random_() * acc)


def mixed(n_keys, total, rng, skew=1.2, scan_frac=0.2):
    """混合负载：(1-scan_frac) Zipf 热点（前 10% 键）+ scan_frac 顺序扫描。

    代表负载：最接近真实生产环境——热点读为主，周期性夹杂
    批处理/备份式全量扫描。用于检验策略的"抗扫描污染"能力。
    """
    hot = max(1, n_keys // 10)
    cdf = []
    acc = 0.0
    for i in range(1, hot + 1):
        acc += 1.0 / (i ** skew)
        cdf.append(acc)
    random_ = rng.random
    bisect_ = bisect.bisect_left
    scan_pos = 0
    for _ in range(total):
        if random_() < scan_frac:
            yield scan_pos % n_keys
            scan_pos += 1
        else:
            yield bisect_(cdf, random_() * acc)


GENERATORS = {
    "scan": scan,
    "uniform": uniform,
    "zipf": zipf,
    "mixed": mixed,
}


def miss_cost(key):
    """单次缺失代价（毫秒）。

    模型：回源耗时随对象大小变化，hash(key) % 10 模拟 1~10ms 的
    异构回源延迟（如不同大小的对象/不同后端）。
    对 int 键 hash(key)==key，行为不变；同时兼容真实轨迹的字符串键。
    确定性函数，保证各策略面对相同的代价分布。
    """
    return 1.0 + (hash(key) % 10)


def load_trace(path):
    """从真实轨迹文件加载（每行一个 key；CSV 取首列）。返回字符串 key 列表的迭代器。"""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line:
                yield line.split(",", 1)[0]
