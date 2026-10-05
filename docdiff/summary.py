"""摘要 <-> 明细 互还原。

- summarize(records)      明细 -> 摘要（分组 + 计数 + 记录 ID 引用）
- expand_summary(s, recs) 摘要 -> 明细（按 ID 引用还原完整记录，顺序一致）
- collapse_summary(s)     摘要 -> 明细存根（rid + kind 投影）
- verify_roundtrip(recs)  验证两个方向均可无损还原，返回验证数据
摘要是确定性函数：同样的明细必得同样的摘要（分组内 ID 有序、组序固定）。
"""
from __future__ import annotations

import hashlib
import json
from typing import Dict, List

GROUPS = ("added", "deleted", "moved", "releveled",
          "renamed", "modified", "carried")


def summarize(records: List[dict]) -> dict:
    groups: Dict[str, List[str]] = {g: [] for g in GROUPS}
    for rec in records:
        groups[rec["kind"]].append(rec["rid"])
    groups = {g: ids for g, ids in groups.items() if ids}
    return {
        "version": 1,
        "total": len(records),
        "stats": {g: len(ids) for g, ids in groups.items()},
        "groups": groups,
    }


def expand_summary(summary: dict, records: List[dict]) -> List[dict]:
    """摘要 + 记录表 -> 完整明细（按 rid 排序，与原明细顺序一致）。"""
    by_id = {r["rid"]: r for r in records}
    ids = [rid for g in GROUPS for rid in summary["groups"].get(g, [])]
    return sorted((by_id[rid] for rid in ids), key=lambda r: r["rid"])


def collapse_summary(summary: dict) -> List[dict]:
    """摘要 -> 明细存根：仅含 rid 与 kind 的投影。"""
    stubs = [{"rid": rid, "kind": g}
             for g in GROUPS for rid in summary["groups"].get(g, [])]
    return sorted(stubs, key=lambda r: r["rid"])


def digest(obj) -> str:
    canon = json.dumps(obj, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def verify_roundtrip(records: List[dict]) -> dict:
    """双向还原验证：摘要->明细、明细->摘要、摘要->存根投影。"""
    s1 = summarize(records)
    restored = expand_summary(s1, records)
    s2 = summarize(restored)
    stubs = collapse_summary(s1)
    projection = [{"rid": r["rid"], "kind": r["kind"]} for r in records]
    return {
        "record_count": len(records),
        "group_counts": s1["stats"],
        "summary_to_detail_ok": restored == records,
        "detail_to_summary_ok": s2 == s1,
        "stub_projection_ok": stubs == projection,
        "detail_sha256": digest(records),
        "summary_sha256": digest(s1),
    }
