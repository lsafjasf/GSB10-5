"""等价类计算与重识别风险量化。

术语：
- 等价类（equivalence class）：在某个 QI 组合上取值完全相同的一组记录。
- k-匿名性：最小等价类大小。k=1 表示存在唯一组合，可直接定位个人。
- 稀有组合：大小 < k_threshold 的等价类，其中的记录为高风险记录。
- 检察官风险（prosecutor risk）：攻击者已知目标在数据集中时，
  最坏情况下命中概率 = 1 / 最小等价类大小。
"""

from collections import defaultdict
from operator import itemgetter


def count_equivalence_classes(rows, key_indices):
    """统计每个等价类的大小。

    rows: 行序列；key_indices: 参与组合的列下标。
    返回 dict: key(tuple) -> 类大小(int)。
    """
    counts = defaultdict(int)
    if len(key_indices) == 1:
        idx = key_indices[0]
        for row in rows:
            counts[row[idx]] += 1
    else:
        getter = itemgetter(*key_indices)
        for row in rows:
            counts[getter(row)] += 1
    return dict(counts)


def evaluate_combination(rows, key_indices, k_threshold=5):
    """对单个字段组合给出量化风险指标。

    返回 dict:
        record_count        记录总数
        class_count         等价类数量
        min_class_size      最小等价类大小（即该组合的 k-匿名值）
        max_class_size      最大等价类大小
        mean_class_size     平均等价类大小
        unique_class_count  大小为 1 的等价类数量（唯一组合）
        unique_record_count 处于唯一组合中的记录数
        rare_class_count    稀有组合数量（大小 < k_threshold）
        rare_record_count   处于稀有组合中的记录数
        rare_record_ratio   稀有记录占比
        prosecutor_risk     检察官风险 = 1 / min_class_size
    """
    counts = count_equivalence_classes(rows, key_indices)
    sizes = list(counts.values())
    n = sum(sizes)
    if not sizes:
        return {
            "record_count": 0, "class_count": 0, "min_class_size": 0,
            "max_class_size": 0, "mean_class_size": 0.0,
            "unique_class_count": 0, "unique_record_count": 0,
            "rare_class_count": 0, "rare_record_count": 0,
            "rare_record_ratio": 0.0, "prosecutor_risk": 0.0,
        }
    min_size = min(sizes)
    unique_classes = sum(1 for s in sizes if s == 1)
    rare_classes = sum(1 for s in sizes if s < k_threshold)
    rare_records = sum(s for s in sizes if s < k_threshold)
    return {
        "record_count": n,
        "class_count": len(sizes),
        "min_class_size": min_size,
        "max_class_size": max(sizes),
        "mean_class_size": n / len(sizes),
        "unique_class_count": unique_classes,
        "unique_record_count": unique_classes,
        "rare_class_count": rare_classes,
        "rare_record_count": rare_records,
        "rare_record_ratio": rare_records / n if n else 0.0,
        "prosecutor_risk": 1.0 / min_size,
    }


def find_risky_records(rows, key_indices, k_threshold=5, id_index=None):
    """定位高风险记录，返回可复核清单。

    两遍扫描：第一遍统计等价类大小，第二遍收集落在稀有组合中的记录，
    避免为安全的大等价类保存成员列表，控制内存。

    返回 list[dict]，按 (类大小, 记录ID) 排序，每项：
        record_id   记录标识（id_index 指定列，缺省为行号）
        row_number  0 起始行号
        class_size  所在等价类大小
        key         该组合的取值元组
    """
    counts = count_equivalence_classes(rows, key_indices)
    if len(key_indices) == 1:
        idx = key_indices[0]
        risky_keys = {key for key, size in counts.items()
                      if size < k_threshold}
        result = []
        for row_no, row in enumerate(rows):
            key = row[idx]
            if key in risky_keys:
                result.append({
                    "record_id": row[id_index] if id_index is not None else row_no,
                    "row_number": row_no,
                    "class_size": counts[key],
                    "key": (key,),
                })
    else:
        getter = itemgetter(*key_indices)
        risky_keys = {key for key, size in counts.items()
                      if size < k_threshold}
        result = []
        for row_no, row in enumerate(rows):
            key = getter(row)
            if key in risky_keys:
                result.append({
                    "record_id": row[id_index] if id_index is not None else row_no,
                    "row_number": row_no,
                    "class_size": counts[key],
                    "key": key,
                })
    result.sort(key=lambda item: (item["class_size"], str(item["record_id"])))
    return result
