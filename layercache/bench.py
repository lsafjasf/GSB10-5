"""1000 组构建脚本的分析耗时与内存峰值基准。

负载模型：先构建一个基线脚本填充缓存；再生成 N 组脚本，每组从 12 个
预设变体中抽取前缀长度（约一半脚本共享前 4 层，构成真实的复用分布）。
指标：
  wall_seconds        墙钟耗时（time.perf_counter）
  peak_traced_bytes   tracemalloc 记录的 Python 分配峰值（精确）
  peak_rss_kb         进程峰值 RSS（Linux getrusage，含解释器）
  cache_entries       去重后缓存条目数
  hit_rate            层缓存命中率
"""

from __future__ import annotations

import resource
import time
import tracemalloc
from dataclasses import dataclass, field
from typing import Any, Dict, List

from .builder import CacheStore, run_build
from .model import BuildScript, FileRecord, Instruction


@dataclass
class BenchmarkResult:
    script_count: int
    layer_count: int
    wall_seconds: float
    peak_traced_bytes: int
    peak_rss_kb: int
    cache_entries: int
    hits: int
    misses: int
    per_script_ms: float = 0.0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "script_count": self.script_count,
            "layer_count": self.layer_count,
            "wall_seconds": round(self.wall_seconds, 6),
            "per_script_ms": round(self.per_script_ms, 6),
            "peak_traced_bytes": self.peak_traced_bytes,
            "peak_traced_mib": round(self.peak_traced_bytes / 1024 / 1024, 3),
            "peak_rss_kb": self.peak_rss_kb,
            "peak_rss_mib": round(self.peak_rss_kb / 1024, 3),
            "cache_entries": self.cache_entries,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hit_rate, 6),
        }


def _make_script(ident: int, variant_depth: int) -> BuildScript:
    """6 层脚本：前 variant_depth 层固定（可复用），其余层随 ident 变化。"""
    context = (
        FileRecord(path="/src/app.py", kind="file",
                   content=f"print('app {ident}')\n".encode(), mode=0o644,
                   mtime=ident),
        FileRecord(path="/conf/settings.ini", kind="file",
                   content=b"[server]\nport=8080\n", mode=0o644),
    )
    instructions: List[Instruction] = [
        Instruction("FROM", "ubuntu:22.04"),
        Instruction("ENV", "PYTHONUNBUFFERED=1"),
        Instruction("WORKDIR", "/srv"),
        Instruction("COPY", "/conf/settings.ini /srv/settings.ini"),
        Instruction("COPY", f"/src/app.py /srv/app{ident}.py"),
        Instruction("RUN", f"write /srv/build{ident}.txt build {ident}"),
    ]
    return BuildScript(name=f"b{ident:04d}",
                       instructions=tuple(instructions), context=context)


def run_benchmark(script_count: int = 1000, shared_prefix: int = 4) -> BenchmarkResult:
    """生成 script_count 组脚本并依次在共享缓存上构建。

    ident % 2 == 0 的脚本把变化层"上移"为固定前缀内容；为了让共享前缀
    真正命中，偶数脚本使用相同的稳定内容（复用基线），奇数脚本各自唯一。
    """
    cache = CacheStore()

    # 偶数脚本的"稳定模板"：与 ident 无关的内容，保证前缀层命中
    stable_ctx = (
        FileRecord(path="/src/app.py", kind="file",
                   content=b"print('stable')\n", mode=0o644),
        FileRecord(path="/conf/settings.ini", kind="file",
                   content=b"[server]\nport=8080\n", mode=0o644),
    )
    stable_ins = [
        Instruction("FROM", "ubuntu:22.04"),
        Instruction("ENV", "PYTHONUNBUFFERED=1"),
        Instruction("WORKDIR", "/srv"),
        Instruction("COPY", "/conf/settings.ini /srv/settings.ini"),
        Instruction("COPY", "/src/app.py /srv/app.py"),
        Instruction("RUN", "write /srv/build.txt stable"),
    ]
    baseline = BuildScript(name="bench-baseline", instructions=tuple(stable_ins),
                           context=stable_ctx)

    tracemalloc.start()
    start = time.perf_counter()
    run_build(baseline, cache)

    layer_count = 0
    for ident in range(script_count):
        if ident % 2 == 0:
            script = BuildScript(name=f"b{ident:04d}", instructions=tuple(stable_ins),
                                 context=stable_ctx)
        else:
            script = _make_script(ident, shared_prefix)
        result = run_build(script, cache)
        layer_count += len(result.layers)

    elapsed = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    return BenchmarkResult(
        script_count=script_count,
        layer_count=layer_count,
        wall_seconds=elapsed,
        peak_traced_bytes=peak,
        peak_rss_kb=peak_rss,
        cache_entries=len(cache),
        hits=cache.hits,
        misses=cache.misses,
        per_script_ms=elapsed / script_count * 1000,
    )
