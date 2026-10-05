"""缓存复用正确性校验。

方法：对同一组脚本分别做
  A) 使用共享缓存的构建（cache=True）
  B) 完全不用缓存的构建（cache=False，等价于逐层重构建）
然后逐层比对"层内容摘要"与"应用该层后的镜像摘要"。
两者全部一致，才说明被复用的层内容与重新构建一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .builder import BuildResult, CacheStore, run_build
from .model import BuildScript


@dataclass(frozen=True)
class LayerCheck:
    index: int
    instruction: str
    reused: bool
    cache_key: str
    content_digest_cached: str
    content_digest_rebuilt: str
    image_digest_cached: str
    image_digest_rebuilt: str

    @property
    def ok(self) -> bool:
        return (self.content_digest_cached == self.content_digest_rebuilt
                and self.image_digest_cached == self.image_digest_rebuilt)


@dataclass
class ReuseVerification:
    name: str
    checks: List[LayerCheck] = field(default_factory=list)
    final_image_match: bool = False
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.final_image_match \
            and all(c.ok for c in self.checks)

    @property
    def reused_layers(self) -> int:
        return sum(1 for c in self.checks if c.reused)


def verify_pair(cached: BuildResult, rebuilt: BuildResult,
                name: str = "") -> ReuseVerification:
    result = ReuseVerification(name=name or cached.name)
    if len(cached.layers) != len(rebuilt.layers):
        result.error = (f"层数不一致: cached={len(cached.layers)} "
                        f"rebuilt={len(rebuilt.layers)}")
        return result
    for layer_cached, layer_rebuilt in zip(cached.layers, rebuilt.layers):
        result.checks.append(LayerCheck(
            index=layer_cached.index,
            instruction=layer_cached.instruction,
            reused=layer_cached.reused,
            cache_key=layer_cached.cache_key,
            content_digest_cached=layer_cached.content_digest,
            content_digest_rebuilt=layer_rebuilt.content_digest,
            image_digest_cached=layer_cached.image_digest,
            image_digest_rebuilt=layer_rebuilt.image_digest,
        ))
    result.final_image_match = cached.image_digest == rebuilt.image_digest
    return result


def verify_script(script: BuildScript, cache: CacheStore) -> ReuseVerification:
    """便捷入口：用给定缓存构建一次，再无缓存构建一次，然后校验。"""
    cached = run_build(script, cache)
    rebuilt = run_build(script, None)
    return verify_pair(cached, rebuilt, name=script.name)
