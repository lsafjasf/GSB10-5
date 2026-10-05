"""组合优化局部搜索库（仅标准库）。

- engine    : 通用爬山引擎、预算（确定性评估次数 / 墙钟时间）、多次运行统计
- maxcut    : 最大割问题 + 单点翻转 / 单点+双点翻转邻域 + 穷举与 Edwards-Erdos 下界
- knapsack  : 0-1 背包问题 + 加一 / 加一+交换邻域 + 穷举与 LP 松弛上界
"""

from .engine import (Budget, BudgetExceeded, SearchResult,
                     local_search, multistart, summarize)
from . import maxcut, knapsack

__all__ = ["Budget", "BudgetExceeded", "SearchResult",
           "local_search", "multistart", "summarize",
           "maxcut", "knapsack"]
