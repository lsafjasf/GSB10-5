"""容器镜像分层缓存分析库（仅标准库）。

公开接口：
    parse_script / parse_file   解析构建脚本
    run_build                   在模拟构建器中构建镜像
    analyze_pair                对比两组脚本，计算复用/重建
    verify_pair                 校验被复用层与重构建层内容一致
    run_benchmark               大量构建脚本的耗时/内存基准
"""

from .parser import BuildScript, FileRecord, parse_file, parse_script
from .builder import BuildResult, CacheStore, Layer, run_build
from .analysis import analyze_pair, ScenarioReport
from .verify import verify_pair
from .bench import run_benchmark

__all__ = [
    "BuildScript",
    "FileRecord",
    "parse_file",
    "parse_script",
    "BuildResult",
    "CacheStore",
    "Layer",
    "run_build",
    "analyze_pair",
    "ScenarioReport",
    "verify_pair",
    "run_benchmark",
]
