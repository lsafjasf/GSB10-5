"""revcheck: 基于语法结构 (AST) 的代码评审检查库。

只使用 Python 标准库。所有检查项都作用于 ast 语法树节点，
而不是对源码做文本匹配，因此字符串字面量与注释中的相似内容
不会被误报。
"""

from .model import Finding
from .engine import check_source, check_file, check_project, ProjectIndex

__all__ = [
    "Finding",
    "check_source",
    "check_file",
    "check_project",
    "ProjectIndex",
]

__version__ = "0.1.0"
