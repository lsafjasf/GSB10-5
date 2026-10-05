"""缓存复用分析：A 构建预热缓存，B 构建判定每层“复用 / 重建”，
并用“整层影子重建（shadow build）”证明每一个复用判定都是正确的。

判定与证明方法
--------------
对 B 的每一层：
1. 计算层键 key = H(父键, 指令指纹)（见 hashing.py）；
2. 若缓存中存在且 blob 完整 => 候选命中：重放层差异；
3. 正确性证明：同时对 B 做一次忽略缓存的完整重建（shadow build），
     - 命中层：重放结果与 shadow 同层的 diff 摘要、层后快照摘要、blob 字节
       三者必须完全一致；
     - 未命中层：原因来自键未命中（指令指纹或父链变化）；
4. 全部 B 层处理完后，缓存重放视图的快照摘要必须等于 shadow 最终快照摘要。

任何证明失败都会抛出 AssertionError —— 也就是说，工具不会在“可能错误
复用”的情况下静默给出乐观结论。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

from .builder import FileSystem, build
from .cache import LayerCache, deserialize_changes, serialize_changes
from .hashing import snapshot_digest
from .model import BuildContext


@dataclass
class LayerVerdict:
    index: int
    instruction: str
    key_b: str
    decision: str                 # reused | rebuilt
    reason: str
    diff_digest_replayed: str | None = None
    diff_digest_shadow: str | None = None
    blob_bytes: int = 0
    digest_match: bool = False
    byte_for_byte_match: bool = False


@dataclass
class AnalysisReport:
    base_image: str
    total_layers: int
    reused_layers: int
    rebuilt_layers: int
    first_rebuilt_index: int | None
    layer_reuse_ratio: float
    byte_reuse_ratio: float
    final_snapshot_match: bool
    final_snapshot_digest: str
    verdicts: list[LayerVerdict] = field(default_factory=list)
    proof: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _miss_reason(op) -> str:
    if op.kind == "copy":
        return "键未命中：COPY 源内容/权限或其上游层发生了语义变化"
    if op.kind == "run":
        return "键未命中：RUN 命令或其上游层发生了语义变化"
    if op.kind == "workdir":
        return "键未命中：WORKDIR 或其上游层发生了语义变化"
    return "键未命中：ENV 或其上游层发生了语义变化"


def analyze(
    script_b: list,
    context_b: BuildContext,
    cache: LayerCache,
    script_a: list | None = None,
    context_a: BuildContext | None = None,
    base_rootfs: dict | None = None,
) -> AnalysisReport:
    """分析 B 相对缓存的复用情况。

    若提供 script_a/context_a，先用 A 构建预热缓存（模拟上一次 CI 构建）。
    内部始终执行 B 的影子构建用于校验。
    """
    if script_a is not None:
        warm = build(script_a, context_a, base_rootfs=base_rootfs)
        warm_wd = "/"
        warm_env: dict[str, str] = {}
        for layer in warm.layers:
            if layer.op.kind == "workdir":
                warm_wd = FileSystem.normalize(layer.op.args[0], warm_wd)
            elif layer.op.kind == "env":
                warm_env = dict(warm_env)
                warm_env[layer.op.args[0]] = layer.op.args[1]
            cache.populate(layer, warm_wd, warm_env)

    shadow = build(script_b, context_b, base_rootfs=base_rootfs)

    fs = FileSystem(dict(base_rootfs or {}))
    workdir = "/"
    env: dict[str, str] = {}
    verdicts: list[LayerVerdict] = []
    total_bytes = 0
    reused_bytes = 0
    first_rebuilt: int | None = None

    for layer in shadow.layers:
        key = layer.key
        entry = cache.lookup(key)
        shadow_blob = serialize_changes(layer.changes)
        total_bytes += len(shadow_blob)

        if entry is not None:
            changes = deserialize_changes(entry.blob)
            for path, record in changes.items():
                fs.apply_change(path, record)
            if entry.op_kind == "workdir":
                workdir = entry.workdir
            elif entry.op_kind == "env":
                env = dict(entry.env)

            replayed_snapshot = snapshot_digest(fs.files)
            diff_match = entry.diff_digest_value == layer.diff_digest_value
            snapshot_match = replayed_snapshot == layer.snapshot_after
            blob_match = entry.blob == shadow_blob
            assert diff_match, f"第 {layer.index} 层：复用层差异摘要与重建不一致"
            assert snapshot_match, f"第 {layer.index} 层：复用后文件系统视图与重建不一致"
            assert blob_match, f"第 {layer.index} 层：复用层字节与重建不一致"

            reused_bytes += len(shadow_blob)
            verdicts.append(
                LayerVerdict(
                    index=layer.index,
                    instruction=layer.op.raw,
                    key_b=key,
                    decision="reused",
                    reason="键命中；差异摘要/快照/字节三重校验一致",
                    diff_digest_replayed=entry.diff_digest_value,
                    diff_digest_shadow=layer.diff_digest_value,
                    blob_bytes=len(shadow_blob),
                    digest_match=True,
                    byte_for_byte_match=True,
                )
            )
        else:
            # 未命中 => 真实构建器会执行指令并落一层新结果：把影子构建在该层
            # 产生的差异与元数据应用到回放视图，保证后续层在正确视图上判定，
            # 并把新层写入缓存（构建即预热）。
            for path, record in layer.changes.items():
                fs.apply_change(path, record)
            if layer.op.kind == "workdir":
                workdir = FileSystem.normalize(layer.op.args[0], workdir)
            elif layer.op.kind == "env":
                env = dict(env)
                env[layer.op.args[0]] = layer.op.args[1]
            cache.populate(layer, workdir, env)
            if first_rebuilt is None:
                first_rebuilt = layer.index
            verdicts.append(
                LayerVerdict(
                    index=layer.index,
                    instruction=layer.op.raw,
                    key_b=key,
                    decision="rebuilt",
                    reason=_miss_reason(layer.op),
                    diff_digest_shadow=layer.diff_digest_value,
                    blob_bytes=len(shadow_blob),
                )
            )

    final_match = snapshot_digest(fs.files) == shadow.final_snapshot
    assert final_match, "缓存重放视图与影子构建最终视图不一致"

    total = len(shadow.layers)
    reused = sum(v.decision == "reused" for v in verdicts)
    return AnalysisReport(
        base_image=shadow.base_image,
        total_layers=total,
        reused_layers=reused,
        rebuilt_layers=total - reused,
        first_rebuilt_index=first_rebuilt,
        layer_reuse_ratio=reused / total if total else 1.0,
        byte_reuse_ratio=reused_bytes / total_bytes if total_bytes else 1.0,
        final_snapshot_match=final_match,
        final_snapshot_digest=shadow.final_snapshot,
        verdicts=verdicts,
        proof={
            "method": "shadow rebuild: 每一层都重新构建一遍并与缓存重放结果比对",
            "checks": "diff 摘要一致 + 层后快照摘要一致 + 层 blob 字节一致 + 最终视图摘要一致",
            "all_passed": True,
        },
    )
