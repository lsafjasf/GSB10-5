"""缓存淘汰策略实现（仅标准库）。

统一接口：
    cache = XxxCache(capacity)
    hit = cache.access(key)          # True=命中, False=缺失（缺失时自动载入）
    n   = cache.bookkeeping_bytes()  # 策略自身元数据的字节数估计

说明：为公平比较，所有策略只存 key（不存 value）。
value 的存储开销对各策略相同，因此不计入元数据对比。
"""

import random
import sys
from collections import OrderedDict, deque


class _LRUNode:
    __slots__ = ("key", "prev", "next")

    def __init__(self, key=None):
        self.key = key
        self.prev = None
        self.next = None


class LRUCache:
    """最近最少使用：哈希表 + 双向链表，命中即移到链表头。"""

    name = "LRU"

    def __init__(self, capacity):
        self.capacity = max(0, capacity)
        self._map = {}
        self._head = _LRUNode()  # 头哨兵：头后 = 最近使用
        self._tail = _LRUNode()  # 尾哨兵：尾前 = 最久未用
        self._head.next = self._tail
        self._tail.prev = self._head

    def _remove(self, node):
        node.prev.next = node.next
        node.next.prev = node.prev

    def _push_front(self, node):
        node.prev = self._head
        node.next = self._head.next
        self._head.next.prev = node
        self._head.next = node

    def access(self, key):
        node = self._map.get(key)
        if node is not None:
            self._remove(node)
            self._push_front(node)
            return True
        if self.capacity == 0:
            return False
        if len(self._map) >= self.capacity:
            victim = self._tail.prev
            self._remove(victim)
            del self._map[victim.key]
        node = _LRUNode(key)
        self._map[key] = node
        self._push_front(node)
        return False

    def __len__(self):
        return len(self._map)

    def bookkeeping_bytes(self):
        # 每个条目一个链表节点（含 prev/next 两个指针），外加 2 个哨兵
        return sys.getsizeof(_LRUNode()) * (len(self._map) + 2)


class LFUCache:
    """最不经常使用：key->频次 + 频次->有序桶，O(1) 淘汰最小频次桶中最旧的 key。"""

    name = "LFU"

    def __init__(self, capacity):
        self.capacity = max(0, capacity)
        self._key_freq = {}            # key -> 访问频次（元数据）
        self._freq_keys = {}           # freq -> OrderedDict[key, None]（同频次按插入序）
        self._min_freq = 0

    def access(self, key):
        freq = self._key_freq.get(key)
        if freq is not None:
            bucket = self._freq_keys[freq]
            del bucket[key]
            if not bucket:
                del self._freq_keys[freq]
                if self._min_freq == freq:
                    self._min_freq = freq + 1
            self._freq_keys.setdefault(freq + 1, OrderedDict())[key] = None
            self._key_freq[key] = freq + 1
            return True
        if self.capacity == 0:
            return False
        if len(self._key_freq) >= self.capacity:
            bucket = self._freq_keys[self._min_freq]
            victim, _ = bucket.popitem(last=False)
            if not bucket:
                del self._freq_keys[self._min_freq]
            del self._key_freq[victim]
        self._key_freq[key] = 1
        self._freq_keys.setdefault(1, OrderedDict())[key] = None
        self._min_freq = 1
        return False

    def __len__(self):
        return len(self._key_freq)

    def bookkeeping_bytes(self):
        # 频次计数（_key_freq 的 value 部分）+ 频次桶结构
        total = sys.getsizeof(self._freq_keys)
        for bucket in self._freq_keys.values():
            total += sys.getsizeof(bucket)
        # 每个 key 的频次整数（小整数有缓存，按对象头保守估计）
        total += 28 * len(self._key_freq)
        return total


class FIFOCache:
    """先进先出：集合 + 队列，命中不改变位置。"""

    name = "FIFO"

    def __init__(self, capacity):
        self.capacity = max(0, capacity)
        self._set = set()
        self._queue = deque()

    def access(self, key):
        if key in self._set:
            return True
        if self.capacity == 0:
            return False
        if len(self._set) >= self.capacity:
            self._set.discard(self._queue.popleft())
        self._set.add(key)
        self._queue.append(key)
        return False

    def __len__(self):
        return len(self._set)

    def bookkeeping_bytes(self):
        # 队列本身（deque 按块分配，getsizeof 已含块内指针）
        return sys.getsizeof(self._queue)


class RandomCache:
    """随机淘汰：字典 + 动态数组，O(1) 随机换出（swap-remove）。"""

    name = "Random"

    def __init__(self, capacity, seed=42):
        self.capacity = max(0, capacity)
        self._set = set()
        self._keys = []
        self._rng = random.Random(seed)

    def access(self, key):
        if key in self._set:
            return True
        if self.capacity == 0:
            return False
        if len(self._keys) >= self.capacity:
            i = self._rng.randrange(len(self._keys))
            victim = self._keys[i]
            last = self._keys.pop()
            if i < len(self._keys):
                self._keys[i] = last
            self._set.discard(victim)
        self._set.add(key)
        self._keys.append(key)
        return False

    def __len__(self):
        return len(self._set)

    def bookkeeping_bytes(self):
        # 候选数组（每条目一个指针槽）
        return sys.getsizeof(self._keys)


ALL_POLICIES = [LRUCache, LFUCache, FIFOCache, RandomCache]
