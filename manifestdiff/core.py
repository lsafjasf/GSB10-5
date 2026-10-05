"""资源清单差异比对核心逻辑。

比对口径详见 docs/SPEC.md，摘要：
- 资源按身份字段 id 匹配；资源在清单中的排列顺序不参与比对。
- 关键字段（KEY_FIELDS）规范化后必须一致，不一致 => 违规差异。
- 允许不同字段（ALLOWED_FIELDS）只做记录 => 合规差异。
- 口径之外的未知字段 => 待确认。
- 仅基线有 / 仅候选有 => 资源缺失（违规）/ 资源新增（待确认）。
- 关键字段缺失单独收集在 key_field_missing，不混入 diffs。
- 等价写法（数值/单位/大小写/列表顺序/k=v 标签等）规范化后一致，
  记为 equivalent_notation 信息项，判定合规，不算差异。
"""
from __future__ import annotations

import json
import re
from typing import Any

# 三级合规判定
VERDICT_COMPLIANT = "compliant"      # 合规差异
VERDICT_VIOLATION = "violation"      # 违规差异
VERDICT_NEEDS_REVIEW = "needs_review"  # 待确认

IDENTITY_FIELD = "id"

# 关键字段：安全/容量/合规属性，规范化后必须一致
KEY_FIELDS = (
    "id",        # 资源身份
    "type",      # 资源类型
    "region",    # 部署地域
    "cpu",       # CPU 核数
    "memory_gb", # 内存（GB）
    "image",     # 镜像
    "encrypted", # 是否加密
    "ports",     # 端口集合
    "tags",      # 标签键值
)

# 允许不同字段：描述性/流程性元数据，不影响合规
ALLOWED_FIELDS = (
    "description",
    "owner",
    "updated_at",
    "remarks",
)

_COMPARED_FIELDS = set(KEY_FIELDS) | set(ALLOWED_FIELDS)


class NormalizeError(ValueError):
    """字段值无法按口径规范化（按违规差异处理）。"""


# ---------------------------------------------------------------- 规范化工具

def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def _as_number(value: Any) -> float:
    if isinstance(value, bool):
        raise NormalizeError("boolean 不是数值")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if re.fullmatch(r"[-+]?\d+(\.\d+)?", text):
            return float(text)
    raise NormalizeError(f"无法解析为数值: {value!r}")


def _norm_cpu(value: Any) -> Any:
    number = _as_number(value)
    return int(number) if number.is_integer() else number


_MEMORY_UNITS = {"": 1.0, "g": 1.0, "gb": 1.0, "m": 1 / 1024, "mb": 1 / 1024}


def _norm_memory(value: Any) -> float:
    """内存统一折算为 GB：16 / "16" / "16GB" / "16384MB" 等价。"""
    if isinstance(value, str):
        match = re.fullmatch(r"\s*([-+]?\d+(?:\.\d+)?)\s*([a-zA-Z]*)\s*", value)
        if not match:
            raise NormalizeError(f"无法解析内存: {value!r}")
        amount, unit = match.group(1), match.group(2).lower()
        if unit not in _MEMORY_UNITS:
            raise NormalizeError(f"未知内存单位: {value!r}")
        gb = float(amount) * _MEMORY_UNITS[unit]
    else:
        gb = _as_number(value)
    return round(gb, 6)


_BOOL_TOKENS = {
    "true": True, "false": False, "yes": True, "no": False,
    "1": True, "0": False, "y": True, "n": False,
}


def _norm_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in _BOOL_TOKENS:
        return _BOOL_TOKENS[value.strip().lower()]
    raise NormalizeError(f"无法解析为布尔值: {value!r}")


def _norm_token(value: Any) -> str:
    """枚举类字符串：去空白、转小写（大小写为等价写法）。"""
    if _is_blank(value):
        raise NormalizeError("空值")
    return str(value).strip().lower()


def _norm_id(value: Any) -> str:
    if _is_blank(value):
        raise NormalizeError("空 id")
    return str(value).strip()


def _norm_ports(value: Any) -> tuple[int, ...]:
    """端口规范化为去重、升序的整数集合；顺序不参与比对。"""
    if isinstance(value, int):
        items = [value]
    elif isinstance(value, str):
        items = [part for part in value.split(",") if part.strip() != ""]
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        raise NormalizeError(f"无法解析端口: {value!r}")
    ports = set()
    for item in items:
        try:
            port = int(str(item).strip())
        except (TypeError, ValueError):
            raise NormalizeError(f"非法端口: {item!r}")
        if not 0 <= port <= 65535:
            raise NormalizeError(f"端口越界: {port}")
        ports.add(port)
    return tuple(sorted(ports))


def _norm_tags(value: Any) -> dict[str, str]:
    """标签规范化为键值字典；["k=v"] 与 {"k": "v"} 等价。"""
    if isinstance(value, dict):
        pairs = value.items()
    elif isinstance(value, list):
        pairs = []
        for item in value:
            if not isinstance(item, str) or "=" not in item:
                raise NormalizeError(f"非法标签写法: {item!r}")
            key, _, val = item.partition("=")
            pairs.append((key, val))
    else:
        raise NormalizeError(f"无法解析标签: {value!r}")
    result = {}
    for key, val in pairs:
        key_s, val_s = str(key).strip(), str(val).strip()
        if key_s == "":
            raise NormalizeError("标签键为空")
        result[key_s] = val_s
    return dict(sorted(result.items()))


_NORMALIZERS = {
    "id": _norm_id,
    "type": _norm_token,
    "region": _norm_token,
    "cpu": _norm_cpu,
    "memory_gb": _norm_memory,
    "image": _norm_token,
    "encrypted": _norm_bool,
    "ports": _norm_ports,
    "tags": _norm_tags,
}


def _norm_allowed(value: Any) -> Any:
    """允许不同字段：字符串仅做首尾空白规范化，其余保持原值。"""
    if isinstance(value, str):
        return value.strip()
    return value


def _raw_repr(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _entry(kind, verdict, reason, resource_id=None, field=None,
           baseline=None, candidate=None, side=None):
    item = {"kind": kind, "verdict": verdict, "reason": reason}
    if resource_id is not None:
        item["resource_id"] = resource_id
    if field is not None:
        item["field"] = field
    if side is not None:
        item["side"] = side
    item["baseline"] = baseline
    item["candidate"] = candidate
    return item


def _extract_resources(manifest: Any) -> list[dict]:
    if isinstance(manifest, list):
        resources = manifest
    elif isinstance(manifest, dict) and isinstance(manifest.get("resources"), list):
        resources = manifest["resources"]
    else:
        raise ValueError("清单必须是资源数组，或含 resources 数组的对象")
    for record in resources:
        if not isinstance(record, dict):
            raise ValueError("资源记录必须是对象")
    return resources


def _index_resources(resources, side, key_field_missing):
    """按规范化 id 建索引；id 缺失/重复单独记录为结构性问题。"""
    indexed = {}
    order = []
    for position, record in enumerate(resources):
        raw_id = record.get(IDENTITY_FIELD)
        if _is_blank(raw_id):
            key_field_missing.append(_entry(
                "missing_key_field", VERDICT_NEEDS_REVIEW,
                "身份字段 id 缺失，无法参与逐条匹配",
                resource_id=None, field=IDENTITY_FIELD, side=side,
                baseline=record if side == "baseline" else None,
                candidate=record if side == "candidate" else None,
            ))
            key_field_missing[-1]["position"] = position
            continue
        rid = _norm_id(raw_id)
        if rid in indexed:
            key_field_missing.append(_entry(
                "duplicate_id", VERDICT_NEEDS_REVIEW,
                "同一清单内 id 重复，无法唯一匹配",
                resource_id=rid, field=IDENTITY_FIELD, side=side,
            ))
            continue
        indexed[rid] = record
        order.append(rid)
    return indexed, order


# ---------------------------------------------------------------- 主比对

def compare_manifests(baseline: Any, candidate: Any) -> dict:
    """比对两份资源清单，返回结构化比对报告。"""
    base_resources = _extract_resources(baseline)
    cand_resources = _extract_resources(candidate)

    key_field_missing: list[dict] = []
    diffs: list[dict] = []
    equivalent_notations: list[dict] = []

    base_index, base_order = _index_resources(
        base_resources, "baseline", key_field_missing)
    cand_index, cand_order = _index_resources(
        cand_resources, "candidate", key_field_missing)

    base_ids, cand_ids = set(base_index), set(cand_index)

    # 资源集合增删
    for rid in sorted(base_ids - cand_ids):
        diffs.append(_entry(
            "resource_missing", VERDICT_VIOLATION,
            "基线存在但候选清单缺失该资源",
            resource_id=rid, baseline=base_index[rid], candidate=None))
    for rid in sorted(cand_ids - base_ids):
        diffs.append(_entry(
            "resource_added", VERDICT_NEEDS_REVIEW,
            "候选清单新增基线中没有的资源，需确认是否审批通过",
            resource_id=rid, baseline=None, candidate=cand_index[rid]))

    # 逐条字段比对
    for rid in sorted(base_ids & cand_ids):
        base_rec, cand_rec = base_index[rid], cand_index[rid]

        for field in KEY_FIELDS:
            if field == IDENTITY_FIELD:
                if _raw_repr(base_rec.get(field)) != _raw_repr(cand_rec.get(field)):
                    equivalent_notations.append(_entry(
                        "equivalent_notation", VERDICT_COMPLIANT,
                        "id 规范化后一致（如首尾空白），不算差异",
                        resource_id=rid, field=field,
                        baseline=base_rec.get(field), candidate=cand_rec.get(field)))
                continue
            raw_b, raw_c = base_rec.get(field), cand_rec.get(field)
            missing_side = None
            if _is_blank(raw_b):
                missing_side = "baseline"
            elif _is_blank(raw_c):
                missing_side = "candidate"
            if missing_side is not None:
                key_field_missing.append(_entry(
                    "missing_key_field", VERDICT_NEEDS_REVIEW,
                    f"关键字段 {field} 缺失或为空，无法判定一致性，需人工确认",
                    resource_id=rid, field=field, side=missing_side,
                    baseline=raw_b, candidate=raw_c))
                continue
            try:
                norm_b = _NORMALIZERS[field](raw_b)
                norm_c = _NORMALIZERS[field](raw_c)
            except NormalizeError as exc:
                diffs.append(_entry(
                    "key_field_unparsable", VERDICT_VIOLATION,
                    f"关键字段 {field} 取值无法按口径解析: {exc}",
                    resource_id=rid, field=field,
                    baseline=raw_b, candidate=raw_c))
                continue
            if norm_b != norm_c:
                diffs.append(_entry(
                    "key_field_mismatch", VERDICT_VIOLATION,
                    f"关键字段 {field} 规范化后不一致",
                    resource_id=rid, field=field,
                    baseline=raw_b, candidate=raw_c))
            elif _raw_repr(raw_b) != _raw_repr(raw_c):
                equivalent_notations.append(_entry(
                    "equivalent_notation", VERDICT_COMPLIANT,
                    f"{field} 仅为等价写法/顺序差异，规范化后一致，不算差异",
                    resource_id=rid, field=field,
                    baseline=raw_b, candidate=raw_c))

        for field in ALLOWED_FIELDS:
            if field not in base_rec and field not in cand_rec:
                continue
            norm_b = _norm_allowed(base_rec.get(field))
            norm_c = _norm_allowed(cand_rec.get(field))
            if norm_b != norm_c:
                diffs.append(_entry(
                    "allowed_field_mismatch", VERDICT_COMPLIANT,
                    f"{field} 属于允许不同的描述性字段，差异合规",
                    resource_id=rid, field=field,
                    baseline=base_rec.get(field), candidate=cand_rec.get(field)))

        unknown_fields = (set(base_rec) | set(cand_rec)) - _COMPARED_FIELDS
        for field in sorted(unknown_fields):
            diffs.append(_entry(
                "unknown_field", VERDICT_NEEDS_REVIEW,
                f"{field} 不在比对口径内，需确认该字段的合规要求",
                resource_id=rid, field=field,
                baseline=base_rec.get(field), candidate=cand_rec.get(field)))

    # 仅资源排列顺序不同 => 合规信息项
    if base_order and base_order != cand_order and base_ids == cand_ids:
        equivalent_notations.append(_entry(
            "resource_order", VERDICT_COMPLIANT,
            "资源在清单中的排列顺序不同，按 id 匹配后完全一致，不算差异",
        ))

    counts = {
        "violation": sum(1 for d in diffs if d["verdict"] == VERDICT_VIOLATION),
        "compliant": sum(1 for d in diffs if d["verdict"] == VERDICT_COMPLIANT),
        "needs_review": sum(1 for d in diffs if d["verdict"] == VERDICT_NEEDS_REVIEW),
    }
    if counts["violation"]:
        overall = VERDICT_VIOLATION
    elif counts["needs_review"] or key_field_missing:
        overall = VERDICT_NEEDS_REVIEW
    else:
        overall = VERDICT_COMPLIANT

    return {
        "summary": {
            "overall_verdict": overall,
            "resources_baseline": len(base_resources),
            "resources_candidate": len(cand_resources),
            "matched_resources": len(base_ids & cand_ids),
            "diff_counts": counts,
            "key_field_missing_count": len(key_field_missing),
            "equivalent_notation_count": len(equivalent_notations),
        },
        "key_field_missing": key_field_missing,
        "diffs": diffs,
        "equivalent_notations": equivalent_notations,
    }


def load_manifest(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)
