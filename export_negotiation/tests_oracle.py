"""独立对拍 oracle（test_oracle.py 使用）。

与 negotiator 的实现刻意不同：不导入 negotiator，只读取 registry 原始数据，
用一套直白、独立重写的规则推导期望结果，用于验证主实现的协商结论与排序。
"""

from .registry import FORMATS, ALIASES


def oracle_negotiate(raw_name, fields):
    # 独立的规范化
    name = raw_name.strip().lower()
    if name in ALIASES:
        name = ALIASES[name]

    norm = {}
    for f in fields:
        if f["name"] not in norm:
            norm[f["name"]] = f["type"]
    field_items = sorted(norm.items())

    def miss(fmt):
        caps = set(FORMATS[fmt]["field_types"])
        return [(n, t) for n, t in field_items if t not in caps]

    exact_hit = name in FORMATS and not miss(name)
    if exact_hit:
        return "exact", name, []

    # 展开降级链（独立的队列展开）
    chain = []
    if name in FORMATS:
        frontier = list(FORMATS[name]["fallbacks"])
    else:
        frontier = []
    while frontier:
        cur = frontier.pop(0)
        if cur not in chain:
            chain.append(cur)
            frontier.extend(FORMATS[cur]["fallbacks"])
    # 族内回退
    if name in FORMATS:
        same = [n for n in FORMATS if FORMATS[n]["kind"] == FORMATS[name]["kind"] and n != name]
        if same:
            chain.append(sorted(same, key=lambda n: (-FORMATS[n]["priority"], n))[0])

    for cand in chain:
        if not miss(cand):
            return "downgraded", cand, []
    if name in FORMATS:
        return "partial", name, miss(name)
    return "failed", None, []
