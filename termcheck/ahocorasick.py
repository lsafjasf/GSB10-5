"""Aho-Corasick 多模式串匹配（纯标准库实现）。

用于在正文中一次性扫描全部术语（中文译名、英文词形），
时间复杂度 O(文本长度 + 命中数)，与模式数量无关，
是 10 万条术语 / 100 万字规模下的标准选择。

构建期用 dict 存转移边，构建完成后 freeze() 压缩为 list 存
(children, fail, outputs)，降低常驻内存。
"""

from collections import deque

__all__ = ["AhoCorasick"]


class AhoCorasick:
    def __init__(self):
        self._children = [{}]   # node -> {symbol: next_node}
        self._outputs = [[]]    # node -> [pattern_id, ...]
        self._fail = None
        self._frozen = False

    def add(self, symbols, pattern_id):
        """symbols: 可迭代的符号序列（字符或 token）。"""
        assert not self._frozen
        node = 0
        for sym in symbols:
            nxt = self._children[node].get(sym)
            if nxt is None:
                nxt = len(self._children)
                self._children.append({})
                self._outputs.append([])
                self._children[node][sym] = nxt
            node = nxt
        self._outputs[node].append(pattern_id)

    def freeze(self):
        """构建失败转移并压缩存储。之后才能 search。"""
        assert not self._frozen
        fail = [0] * len(self._children)
        queue = deque()
        for sym, nxt in self._children[0].items():
            fail[nxt] = 0
            queue.append(nxt)
        while queue:
            node = queue.popleft()
            for sym, nxt in self._children[node].items():
                queue.append(nxt)
                f = fail[node]
                while f and sym not in self._children[f]:
                    f = fail[f]
                fail[nxt] = self._children[f].get(sym, 0)
                if fail[nxt] == nxt:
                    fail[nxt] = 0
                # 合并输出（沿失败链的模式是当前模式的后缀）
                if self._outputs[fail[nxt]]:
                    self._outputs[nxt].extend(self._outputs[fail[nxt]])
        # 压缩为 (children_dict, outputs_list) 元组，丢弃中间结构
        self._nodes = [(c, o) for c, o in zip(self._children, self._outputs)]
        self._fail = fail
        self._children = None
        self._outputs = None
        self._frozen = True

    def search(self, symbols):
        """产出 (end_index, pattern_id)，end_index 为匹配终点（含）。"""
        assert self._frozen
        nodes = self._nodes
        fail = self._fail
        node = 0
        for i, sym in enumerate(symbols):
            while node and sym not in nodes[node][0]:
                node = fail[node]
            node = nodes[node][0].get(sym, 0)
            for pid in nodes[node][1]:
                yield i, pid
