"""Helpers to measure pre/post-merge cardinality (exact for small data,
HLL-based for the 10M benchmark)."""

from __future__ import annotations

from .sketch import HyperLogLog


def measure_merged(stream, policy, top_values=None, exact_limit: int = 1_000_000):
    """Consume an iterable of label dicts after merging.

    Returns (merged_series_cardinality, per_label_distinct_dict).
    Exact sets are used while the merged tuple set stays below exact_limit,
    after which only HLLs are retained (bounded memory on 10M series).
    """
    from .policy import merge_labels

    exact = set()
    hll = HyperLogLog(14)
    label_exact: dict = {}
    label_hll: dict = {}
    use_exact = True

    for labels in stream:
        merged = merge_labels(labels, policy, top_values)
        key = tuple(sorted(merged.items()))
        if use_exact:
            exact.add(key)
            for k, v in merged.items():
                label_exact.setdefault(k, set()).add(v)
            if len(exact) > exact_limit:
                use_exact = False
                for tk, s in label_exact.items():
                    h = HyperLogLog(12)
                    for v in s:
                        h.add(v)
                    label_hll[tk] = h
                for tk_tuple in exact:
                    hll.add(tk_tuple)
                exact = None
                label_exact = None
        else:
            hll.add(key)
            for k, v in merged.items():
                h = label_hll.get(k)
                if h is None:
                    h = label_hll[k] = HyperLogLog(12)
                h.add((k, v))

    if use_exact:
        return len(exact), {k: len(v) for k, v in label_exact.items()}
    return hll.estimate(), {k: h.estimate() for k, h in label_hll.items()}


def raw_series_hll(stream_iter, p: int = 14):
    """Estimate raw (pre-merge) distinct series count of a label stream."""
    hll = HyperLogLog(p)
    n = 0
    for labels in stream_iter:
        hll.add(tuple(sorted(labels.items())))
        n += 1
    return n, hll.estimate()
