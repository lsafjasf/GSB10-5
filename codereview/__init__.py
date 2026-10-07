"""基于 AST 的代码评审检查库（仅依赖 Python 3 标准库）。"""

from .checker import Finding, check_paths, check_source

__all__ = ["Finding", "check_paths", "check_source"]
