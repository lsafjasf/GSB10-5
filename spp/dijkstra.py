"""Naive Dijkstra shortest paths (the baseline every query would pay)."""
import heapq

INF = float("inf")


def dijkstra(graph, source, target=None, reverse=False):
    """Single-source shortest paths.

    If ``target`` is None, returns the full distance list.
    Otherwise returns the distance to ``target`` and stops as soon as the
    target is settled (early exit), which is the cheapest exact way to
    answer one pair without any preprocessing.
    """
    n = graph.n
    dist = [INF] * n
    dist[source] = 0.0
    pq = [(0.0, source)]
    if reverse:
        offsets, targets, weights = graph.roffsets, graph.rtargets, graph.rweights
    else:
        offsets, targets, weights = graph.offsets, graph.targets, graph.weights
    push = heapq.heappush
    pop = heapq.heappop
    while pq:
        d, u = pop(pq)
        if d > dist[u]:
            continue
        if target is not None and u == target:
            return d
        for i in range(offsets[u], offsets[u + 1]):
            nd = d + weights[i]
            v = targets[i]
            if nd < dist[v]:
                dist[v] = nd
                push(pq, (nd, v))
    if target is not None:
        return dist[target]
    return dist
