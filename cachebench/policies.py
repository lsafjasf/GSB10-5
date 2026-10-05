"""缓存淘汰策略实现（仅标准库）。

统一接口：
    pol = Policy(capacity)
    hit = pol.access(key, miss_cost)   # True=命中, False=缺失(并按 miss_cost 累加缺失代价)

每个策略都统计：
    hits / misses      命中与缺失次数
    miss_cost          缺失代价加权和（缺失代价由负载侧给出，代表回源延迟/对象大小等）

四种策略的元数据开销（每个缓存条目的额外结构）：
    LRU    : OrderedDict 内部双向链表节点（每键一个链表节点 + 哈希表项）
    LFU    : 频率计数 + 频率桶 OrderedDict（每键一个哈希项 + 一个桶内链表节点）
    FIFO   : 哈希 set + deque 两端的各一份引用
    Random : 哈希表项 + 数组槽位（key -> 下标）
"""

from collections import OrderedDict, deque
import random


class BasePolicy:
    name = "base"

    def __init__(self, capacity):
        if capacity < 0:
            raise ValueError("capacity must be >= 0")
        self.capacity = capacity
        self.hits = 0
        self.misses = 0
        self.miss_cost = 0.0

    def access(self, key, cost):
        raise NotImplementedError

    def __len__(self):
        raise NotImplementedError


class LRU(BasePolicy):
    """最近最少使用。适用：存在时间局部性的通用负载（Web 页面、API 结果）。
    弱点：顺序扫描会污染整个缓存（scan-resistant 差）。"""

    name = "LRU"

    def __init__(self, capacity):
        super().__init__(capacity)
        self._od = OrderedDict()

    def access(self, key, cost):
        od = self._od
        if key in od:
            od.move_to_end(key)
            self.hits += 1
            return True
        self.misses += 1
        self.miss_cost += cost
        if self.capacity > 0:
            od[key] = None
            if len(od) > self.capacity:
                od.popitem(last=False)
        return False

    def __len__(self):
        return len(self._od)


class LFU(BasePolicy):
    """最不经常使用（同频按 LRU 淘汰）。适用：热点稳定且长期不变的负载
    （CDN 对象、词典/配置项）。弱点：频率有"记忆"，热点切换后旧热点
    需要一段时间才会被淘汰；顺序扫描同样抖动。"""

    name = "LFU"

    def __init__(self, capacity):
        super().__init__(capacity)
        self._key_freq = {}      # key -> freq
        self._freq_keys = {}     # freq -> OrderedDict[key, None]，桶内按插入先后（LRU）
        self._min_freq = 0

    def access(self, key, cost):
        kf = self._key_freq
        f = kf.get(key)
        if f is not None:
            self.hits += 1
            bucket = self._freq_keys[f]
            del bucket[key]
            if not bucket:
                del self._freq_keys[f]
                if self._min_freq == f:
                    self._min_freq = f + 1
            nf = f + 1
            self._freq_keys.setdefault(nf, OrderedDict())[key] = None
            kf[key] = nf
            return True
        self.misses += 1
        self.miss_cost += cost
        if self.capacity == 0:
            return False
        if len(kf) >= self.capacity:
            bucket = self._freq_keys[self._min_freq]
            evict_key, _ = bucket.popitem(last=False)
            if not bucket:
                del self._freq_keys[self._min_freq]
            del kf[evict_key]
        kf[key] = 1
        self._freq_keys.setdefault(1, OrderedDict())[key] = None
        self._min_freq = 1
        return False

    def __len__(self):
        return len(self._key_freq)


class FIFO(BasePolicy):
    """先进先出。适用：实现最简单、元数据最省，适合对命中率不敏感、
    只需限制内存的场景（消息缓冲、写前日志）。弱点：不看访问历史，
    热点键照样会被淘汰。"""

    name = "FIFO"

    def __init__(self, capacity):
        super().__init__(capacity)
        self._set = set()
        self._dq = deque()

    def access(self, key, cost):
        if key in self._set:
            self.hits += 1
            return True
        self.misses += 1
        self.miss_cost += cost
        if self.capacity > 0:
            self._set.add(key)
            self._dq.append(key)
            if len(self._dq) > self.capacity:
                self._set.discard(self._dq.popleft())
        return False

    def __len__(self):
        return len(self._set)


class RandomPolicy(BasePolicy):
    """随机淘汰。适用：无局部性或访问模式对缓存"敌对"的负载
    （如大顺序扫描、均匀随机），以及需要极低实现/维护成本时。
    理论依据：对任意访问序列，随机淘汰不会被构造出最坏情况。"""

    name = "Random"

    def __init__(self, capacity, seed=42):
        super().__init__(capacity)
        self._index = {}         # key -> 在 _keys 中的下标
        self._keys = []          # 槽位数组，淘汰时 O(1) 替换
        self._rng = random.Random(seed)

    def access(self, key, cost):
        idx = self._index
        if key in idx:
            self.hits += 1
            return True
        self.misses += 1
        self.miss_cost += cost
        if self.capacity == 0:
            return False
        keys = self._keys
        if len(keys) < self.capacity:
            idx[key] = len(keys)
            keys.append(key)
        else:
            victim_pos = self._rng.randrange(self.capacity)
            del idx[keys[victim_pos]]
            keys[victim_pos] = key
            idx[key] = victim_pos
        return False

    def __len__(self):
        return len(self._index)


ALL_POLICIES = [LRU, LFU, FIFO, RandomPolicy]
