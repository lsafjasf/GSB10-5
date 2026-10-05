"""Compressed-sparse-row (CSR) directed weighted graph. Stdlib only."""
from array import array


class Graph:
    """Directed graph with non-negative float weights.

    Stores both forward and reverse adjacency in CSR layout so that
    backward searches (needed by landmark preprocessing) are cheap.
    """

    __slots__ = ("n", "m", "offsets", "targets", "weights",
                 "roffsets", "rtargets", "rweights", "_edges")

    def __init__(self, num_nodes):
        if num_nodes <= 0:
            raise ValueError("num_nodes must be positive")
        self.n = num_nodes
        self.m = 0
        self._edges = []

    def add_edge(self, u, v, w):
        if not (0 <= u < self.n) or not (0 <= v < self.n):
            raise ValueError("endpoint out of range")
        if w < 0:
            raise ValueError("negative weights are not supported (Dijkstra-based)")
        self._edges.append((u, v, float(w)))

    @classmethod
    def from_edges(cls, num_nodes, edges):
        g = cls(num_nodes)
        for u, v, w in edges:
            g.add_edge(u, v, w)
        return g.finalize()

    def finalize(self):
        fwd = self._build_csr(self._edges)
        rev = self._build_csr([(v, u, w) for (u, v, w) in self._edges])
        (self.offsets, self.targets, self.weights) = fwd
        (self.roffsets, self.rtargets, self.rweights) = rev
        self.m = len(self._edges)
        self._edges = None
        return self

    def _build_csr(self, edges):
        offsets = array('q', [0]) * (self.n + 1)
        for u, _, _ in edges:
            offsets[u + 1] += 1
        for i in range(self.n):
            offsets[i + 1] += offsets[i]
        targets = array('q', [0]) * len(edges)
        weights = array('d', [0.0]) * len(edges)
        cursor = array('q', offsets)
        for u, v, w in edges:
            pos = cursor[u]
            targets[pos] = v
            weights[pos] = w
            cursor[u] = pos + 1
        return offsets, targets, weights

    def memory_bytes(self):
        """Bytes held by the CSR structures (forward + reverse)."""
        per_dir = (self.n + 1) * 8 + self.m * (8 + 8)
        return 2 * per_dir
