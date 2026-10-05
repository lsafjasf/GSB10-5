"""di: 一个只依赖标准库的依赖注入容器（解析顺序 / 环报告 / 延迟注入 / 失败回收）。"""
from .container import (
    CircularDependencyError,
    ConstructionError,
    Container,
    CycleInfo,
    CycleReport,
    DIError,
    LazyCycleError,
    LazyDep,
    LazyRef,
    MissingDependencyError,
    lazy,
)
from .trace import Trace

__all__ = [
    "CircularDependencyError",
    "ConstructionError",
    "Container",
    "CycleInfo",
    "CycleReport",
    "DIError",
    "LazyCycleError",
    "LazyDep",
    "LazyRef",
    "MissingDependencyError",
    "Trace",
    "lazy",
]
