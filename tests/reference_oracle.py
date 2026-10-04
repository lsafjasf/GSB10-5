"""与库独立的参考实现（oracle），用于对拍。

- 数据副本硬编码在本文件中（test 会校验它与 registry 一致，防漂移）；
- 算法用最直白的命令式写法重新实现，不复用 negotiator 任何代码；
- 只输出四元组 (status, format, exportable, missing_names)，
  对拍只比对“确定的协商结论”。
"""

ORACLE_FORMATS = {
    "json": (5, "json",
             {"str", "int", "float", "bool", "date", "datetime", "object", "array"}),
    "ndjson": (15, "json",
               {"str", "int", "float", "bool", "date", "datetime", "object", "array"}),
    "csv": (10, "tabular",
            {"str", "int", "float", "bool", "date", "datetime"}),
    "tsv": (20, "tabular",
            {"str", "int", "float", "bool", "date", "datetime"}),
}
ORACLE_ALIASES = {"jsonl": "ndjson"}
ORACLE_FALLBACK = {"xlsx": "tabular", "xls": "tabular", "ods": "tabular"}

EXACT = "exact"
DOWNGRADED = "downgraded"
PARTIAL = "partial"
UNSUPPORTED = "unsupported"


def _rank(family=None):
    rows = [
        (pref, fid, fam, types)
        for fid, (pref, fam, types) in ORACLE_FORMATS.items()
        if family is None or fam == family
    ]
    rows.sort(key=lambda r: (r[0], r[1]))
    return rows


def negotiate(requested, fields):
    types = [t for _, t in fields]
    names = [n for n, _ in fields]

    canonical = ORACLE_ALIASES.get(requested, requested)
    if canonical in ORACLE_FORMATS:
        _, fam, supported = ORACLE_FORMATS[canonical]
        ok = [n for n, t in zip(names, types) if t in supported]
        miss = [n for n, t in zip(names, types) if t not in supported]
        return (PARTIAL if miss else EXACT, canonical, ok, miss)

    fam = ORACLE_FALLBACK.get(requested)
    pool = _rank(fam)

    def cover(row):
        supported = row[3]
        return sum(1 for t in types if t in supported)

    ranked = sorted(pool, key=lambda r: (-cover(r), r[0], r[1]))
    if not ranked or cover(ranked[0]) == 0:
        return (UNSUPPORTED, None, [], list(names))

    pref, fid, rfam, supported = ranked[0]
    ok = [n for n, t in zip(names, types) if t in supported]
    miss = [n for n, t in zip(names, types) if t not in supported]
    if miss:
        return (PARTIAL, fid, ok, miss)
    return (DOWNGRADED, fid, ok, miss)
