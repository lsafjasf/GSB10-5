"""两组构建脚本的缓存复用对比分析。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .builder import BuildResult, CacheStore, run_build
from .model import BuildScript
from .verify import ReuseVerification, verify_pair


@dataclass
class LayerVerdict:
    index: int
    instruction: str
    verdict: str                    # "reuse" | "rebuild"
    reason: str
    cache_key: str


@dataclass
class ScenarioReport:
    name: str
    base_name: str
    cand_name: str
    total_layers: int = 0
    reused_layers: int = 0
    rebuilt_layers: int = 0
    layer_reuse_rate: float = 0.0
    total_bytes: int = 0
    reused_bytes: int = 0
    byte_reuse_rate: float = 0.0
    first_divergence_index: Optional[int] = None
    instruction_overlap: float = 0.0
    verdicts: List[LayerVerdict] = field(default_factory=list)
    verification: Optional[ReuseVerification] = None

    @property
    def verified(self) -> bool:
        return self.verification is not None and self.verification.ok


def _first_divergence(a: BuildResult, b: BuildResult) -> Optional[int]:
    for layer_a, layer_b in zip(a.layers, b.layers):
        if layer_a.cache_key != layer_b.cache_key:
            return layer_a.index
    if len(a.layers) != len(b.layers):
        return min(len(a.layers), len(b.layers))
    return None


def analyze_pair(base: BuildScript, candidate: BuildScript,
                 cache: Optional[CacheStore] = None,
                 name: str = "") -> ScenarioReport:
    """先构建 base 填充缓存，再构建 candidate 并统计复用情况。

    同时用"无缓存重建 candidate"做逐层内容校验。
    """
    store = cache if cache is not None else CacheStore()
    base_result = run_build(base, store)
    cand_cached = run_build(candidate, store)
    cand_rebuilt = run_build(candidate, None)

    report = ScenarioReport(name=name or f"{base.name} -> {candidate.name}",
                            base_name=base.name, cand_name=candidate.name)
    report.total_layers = len(cand_cached.layers)
    report.reused_layers = cand_cached.reused_count
    report.rebuilt_layers = report.total_layers - report.reused_layers
    report.layer_reuse_rate = (report.reused_layers / report.total_layers
                               if report.total_layers else 0.0)
    report.total_bytes = sum(l.size_bytes for l in cand_cached.layers)
    report.reused_bytes = sum(l.size_bytes for l in cand_cached.layers if l.reused)
    report.byte_reuse_rate = (report.reused_bytes / report.total_bytes
                              if report.total_bytes else 0.0)
    report.first_divergence_index = _first_divergence(base_result, cand_cached)

    base_set = set(base.instruction_texts())
    cand_set = set(candidate.instruction_texts())
    union = base_set | cand_set
    report.instruction_overlap = (len(base_set & cand_set) / len(union)
                                  if union else 1.0)

    diverged = False
    prev_key: Optional[str] = None
    base_keys = {l.cache_key for l in base_result.layers}
    for layer in cand_cached.layers:
        if layer.reused:
            verdict, reason = "reuse", "缓存键命中，层内容经校验与重建一致"
        else:
            verdict = "rebuild"
            if not diverged and layer.cache_key not in base_keys:
                reason = "首个分叉层：指令或其输入（内容/权限/父链）发生变化"
                diverged = True
            else:
                reason = "父层已变化，链式缓存键随之失效"
        report.verdicts.append(LayerVerdict(
            index=layer.index, instruction=layer.instruction,
            verdict=verdict, reason=reason, cache_key=layer.cache_key))
        prev_key = layer.cache_key

    report.verification = verify_pair(cand_cached, cand_rebuilt,
                                      name=candidate.name)
    return report
