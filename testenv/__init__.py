"""testenv: 并行测试环境隔离与回收库（仅标准库）。"""
from .isolation import (
    IsolatedEnv,
    ReclaimError,
    SweepReport,
    destroy_all,
    isolated_env,
    live_namespaces,
    sweep_residue,
)

__all__ = [
    "IsolatedEnv",
    "ReclaimError",
    "SweepReport",
    "destroy_all",
    "isolated_env",
    "live_namespaces",
    "sweep_residue",
]
__version__ = "1.0.0"
