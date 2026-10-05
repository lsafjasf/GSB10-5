"""降低重识别风险的两类标准手段：泛化（generalization）与抑制（suppression）。

- 泛化：按层级把取值替换为更粗粒度的形式，如
  年龄 34 -> "30-39"，邮编 100084 -> "100***"，职业 -> 职业大类。
  泛化会增大等价类、降低信息精度，但不删除记录。
- 抑制：对泛化后仍落在稀有组合中的记录，把其 QI 取值整体替换为掩码，
  使这些记录彼此不可区分（或可选地整行删除）。
"""

from operator import itemgetter

from .risk import count_equivalence_classes


MASK = "*"


def age_band(width=10):
    """年龄泛化：34 -> '30-39'。无法解析时原样返回。"""
    def generalize(value):
        try:
            age = int(value)
        except (TypeError, ValueError):
            return value
        low = (age // width) * width
        return f"{low}-{low + width - 1}"
    return generalize


def zip_prefix(keep=3):
    """邮编泛化：保留前 keep 位，其余用 * 替代，如 100084 -> '100***'。"""
    def generalize(value):
        text = str(value)
        if len(text) <= keep:
            return text
        return text[:keep] + MASK * (len(text) - keep)
    return generalize


def category_mapper(mapping, default="other"):
    """取值到类别的映射泛化，如 职业 -> 职业大类；未命中归入 default。"""
    def generalize(value):
        return mapping.get(value, default)
    return generalize


def apply_generalization(rows, column_funcs):
    """对指定列应用泛化函数，返回新行列表（不修改原数据）。

    column_funcs: {列下标: 泛化函数}
    """
    if not column_funcs:
        return [tuple(row) for row in rows]
    out = []
    for row in rows:
        new_row = list(row)
        for idx, func in column_funcs.items():
            new_row[idx] = func(new_row[idx])
        out.append(tuple(new_row))
    return out


def suppress_risky_records(rows, key_indices, k_threshold=5, mask=MASK):
    """抑制：把落在稀有组合（大小 < k_threshold）中的记录的
    组合列取值整体替换为掩码，返回 (新行列表, 被抑制记录数)。

    被抑制的记录在组合列上取值相同（全为掩码），彼此不可区分，
    重新评估时它们构成一个大的“已抑制”等价类。
    """
    counts = count_equivalence_classes(rows, key_indices)
    if len(key_indices) == 1:
        idx = key_indices[0]
        risky_keys = {key for key, size in counts.items()
                      if size < k_threshold}
        out = []
        suppressed = 0
        for row in rows:
            if row[idx] in risky_keys:
                new_row = list(row)
                new_row[idx] = mask
                out.append(tuple(new_row))
                suppressed += 1
            else:
                out.append(tuple(row))
        return out, suppressed

    risky_keys = {key for key, size in counts.items() if size < k_threshold}
    getter = itemgetter(*key_indices)
    out = []
    suppressed = 0
    for row in rows:
        key = getter(row)
        if key in risky_keys:
            new_row = list(row)
            for i in key_indices:
                new_row[i] = mask
            out.append(tuple(new_row))
            suppressed += 1
        else:
            out.append(tuple(row))
    return out, suppressed
