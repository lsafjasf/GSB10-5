"""查询敏感度（sensitivity）计算。

敏感度定义：在相邻数据集（相差一条记录）上，查询真实答案的最大变化量。
所有噪声量都由敏感度推导，因此这里是隐私保护的根基。

支持的查询类型
--------------
count     : 计数查询。一条记录最多影响计数 1            -> L1 敏感度 = 1
sum       : 有界求和。字段值被裁剪到 [lo, hi]           -> L1 敏感度 = hi - lo
mean      : 有界均值 = sum / count。sum 与 count 各计一次敏感度
            （消耗 2 份预算，见 BUDGET_RULES.md 第 3 节）
histogram : 直方图。一条记录只落入一个桶，L1 变化为 1   -> L1 敏感度 = 1
"""

COUNT_SENSITIVITY = 1.0
HISTOGRAM_SENSITIVITY = 1.0


def count_sensitivity() -> float:
    """计数查询的 L1 敏感度：一条记录最多使计数变化 1。"""
    return COUNT_SENSITIVITY


def sum_sensitivity(lo: float, hi: float) -> float:
    """有界求和的 L1 敏感度：值域宽度 hi - lo。

    值在查询前会被裁剪到 [lo, hi]，因此单条记录对总和的影响
    不超过 hi - lo。
    """
    if hi <= lo:
        raise ValueError(f"求和边界非法：需要 hi > lo，得到 lo={lo}, hi={hi}")
    return float(hi - lo)


def mean_sensitivity(lo: float, hi: float) -> tuple:
    """有界均值的敏感度分解：(sum 部分, count 部分)。

    mean 按 sum + count 两个子查询回答，各消耗一份预算：
      sum   敏感度 = hi - lo
      count 敏感度 = 1
    """
    return sum_sensitivity(lo, hi), COUNT_SENSITIVITY


def histogram_sensitivity() -> float:
    """直方图的 L1 敏感度：一条记录只进入一个桶，L1 变化为 1。

    注意是 L1 而非 L2：相邻数据集间直方图的 L1 距离为 1
    （一个桶 +1 等价于整条记录只贡献一个桶），因此所有桶
    共用同一份噪声预算，按一次查询计费。
    """
    return HISTOGRAM_SENSITIVITY
