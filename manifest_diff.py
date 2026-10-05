#!/usr/bin/env python3
"""资源清单差异比对库与命令行工具（仅使用 Python 标准库）。

比对口径与合规判定规则的完整说明见 CRITERIA.md。
基本用法：
    from manifest_diff import compare_manifests
    result = compare_manifests(manifest_baseline, manifest_actual)
"""

from __future__ import annotations

import argparse
import json
import re
import sys

# ---------------------------------------------------------------------------
# 比对口径（依据见 CRITERIA.md 第 3 节）
# ---------------------------------------------------------------------------

# 关键字段：决定资源身份与完整性，必须一致；不一致判定为违规差异。
CRITICAL_FIELDS = ("name", "type", "version", "checksum")
# 信息字段：仅具描述性，允许不同；不一致判定为合规差异。
INFORMATIONAL_FIELDS = ("description", "tags", "updated_at", "source")
# 不在上述两组内的字段：口径未覆盖，不一致判定为待确认。

# 条目配对键：两边清单按 (name, type) 对齐，顺序无关。
KEY_FIELDS = ("name", "type")

# 合规判定
VERDICT_COMPLIANT = "合规差异"
VERDICT_VIOLATION = "违规差异"
VERDICT_PENDING = "待确认"

# 差异归因类别
CAT_FIELD_MISMATCH = "field_mismatch"               # 字段值不一致
CAT_ENTRY_MISSING = "entry_missing"                 # 基准侧有、对方侧无
CAT_ENTRY_EXTRA = "entry_extra"                     # 对方侧有、基准侧无
CAT_MISSING_CRITICAL = "missing_critical_field"     # 关键字段缺失
CAT_DUPLICATE_KEY = "duplicate_key"                 # 同侧配对键重复
CAT_BAD_ENTRY = "bad_entry"                         # 条目不是对象

_ABSENT = object()  # 字段在某一侧不存在


# ---------------------------------------------------------------------------
# 等价写法规整（CRITERIA.md 第 5 节）：规整后相等即视为同一取值
# ---------------------------------------------------------------------------

_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?\d+\.\d+$")


def _norm_number(value):
    """数值型字段：数字字符串与数字等价，整数 1024 与字符串 "1024" 等价。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if _INT_RE.match(text):
            return int(text)
        if _FLOAT_RE.match(text):
            return float(text)
    return value


def _norm_version(value):
    """版本号：允许 v/V 前缀等价（v1.2.0 == 1.2.0），忽略首尾空白。"""
    if isinstance(value, str):
        text = value.strip()
        if len(text) > 1 and text[0] in ("v", "V") and text[1].isdigit():
            return text[1:]
        return text
    return value


def _norm_checksum(value):
    """校验和：十六进制大小写不敏感，忽略冒号/连字符/空白分隔符。"""
    if isinstance(value, str):
        return re.sub(r"[\s:\-]", "", value).lower()
    return value


def _norm_path(value):
    """路径：首尾空白、反斜杠、重复斜杠、结尾斜杠不构成差异。"""
    if isinstance(value, str):
        text = value.strip().replace("\\", "/")
        text = re.sub(r"/{2,}", "/", text)
        if len(text) > 1:
            text = text.rstrip("/") or "/"
        return text
    return value


_FIELD_NORMALIZERS = {
    "version": _norm_version,
    "checksum": _norm_checksum,
    "path": _norm_path,
    "size": _norm_number,
}


def normalize_value(field, value):
    """按字段语义规整取值；通用规则见 CRITERIA.md 第 5 节。"""
    if value is None:
        return None
    if field in _FIELD_NORMALIZERS:
        return _FIELD_NORMALIZERS[field](value)
    if isinstance(value, str):
        text = value.strip()
        if text.lower() == "true":
            return True
        if text.lower() == "false":
            return False
        return text
    if isinstance(value, list):
        # 有序数组外的列表（如 tags）按多重集合比较，元素顺序无关。
        return sorted((normalize_value(field, item) for item in value), key=repr)
    if isinstance(value, dict):
        # 对象字段顺序天然无关：dict 相等只比较键值。
        return {key: normalize_value(key, val) for key, val in value.items()}
    return value


# ---------------------------------------------------------------------------
# 比对主体
# ---------------------------------------------------------------------------

def _extract_resources(manifest):
    """支持两种清单形态：资源数组本身，或 {"resources": [...]} 对象。"""
    if isinstance(manifest, list):
        return manifest
    if isinstance(manifest, dict) and isinstance(manifest.get("resources"), list):
        return manifest["resources"]
    raise ValueError("清单格式不支持：应为资源数组或包含 resources 数组的对象")


def _index_entries(resources, side, missing_critical, pending):
    """按配对键索引一侧清单；无法配对的条目归入关键字段缺失。"""
    index = {}
    for pos, entry in enumerate(resources):
        if not isinstance(entry, dict):
            pending.append({
                "category": CAT_BAD_ENTRY,
                "verdict": VERDICT_PENDING,
                "side": side,
                "entry_index": pos,
                "resource": None,
                "reason": "该条目不是对象，无法解析",
            })
            continue
        absent = [f for f in KEY_FIELDS if entry.get(f) in (None, "")]
        if absent:
            missing_critical.append({
                "category": CAT_MISSING_CRITICAL,
                "verdict": VERDICT_VIOLATION,
                "side": side,
                "entry_index": pos,
                "resource": None,
                "missing_fields": absent,
                "reason": "关键字段缺失：%s（该条目无法参与配对）" % ", ".join(absent),
            })
            continue
        key = (entry["name"], entry["type"])
        if key in index:
            pending.append({
                "category": CAT_DUPLICATE_KEY,
                "verdict": VERDICT_PENDING,
                "side": side,
                "resource": {"name": key[0], "type": key[1]},
                "reason": "同侧清单内配对键 (name, type) 重复，配对结果不确定",
            })
            continue
        index[key] = (pos, entry)
    return index


def _field_mismatch_record(key, field, value_a, value_b, verdict, reason):
    return {
        "category": CAT_FIELD_MISMATCH,
        "verdict": verdict,
        "resource": {"name": key[0], "type": key[1]},
        "field": field,
        "value_a": value_a,
        "value_b": value_b,
        "reason": reason,
    }


def _compare_entry(key, entry_a, entry_b, violations, compliant_diffs,
                   pending, missing_critical):
    resource = {"name": key[0], "type": key[1]}

    # 关键字段：缺失单独报出；取值不一致记违规。
    for field in CRITICAL_FIELDS:
        if field in KEY_FIELDS:
            continue
        raw_a = entry_a.get(field, _ABSENT)
        raw_b = entry_b.get(field, _ABSENT)
        absent_sides = []
        if raw_a is _ABSENT or raw_a in (None, ""):
            absent_sides.append("a")
        if raw_b is _ABSENT or raw_b in (None, ""):
            absent_sides.append("b")
        if absent_sides:
            missing_critical.append({
                "category": CAT_MISSING_CRITICAL,
                "verdict": VERDICT_VIOLATION,
                "side": "+".join(absent_sides),
                "entry_index": None,
                "resource": resource,
                "missing_fields": [field],
                "reason": "关键字段 %s 在 %s 侧缺失" % (
                    field, "、".join(absent_sides)),
            })
            continue
        if normalize_value(field, raw_a) != normalize_value(field, raw_b):
            violations.append(_field_mismatch_record(
                key, field, raw_a, raw_b, VERDICT_VIOLATION,
                "关键字段 %s 不一致，实际部署物与基准不是同一构建" % field))

    # 其余字段：信息字段允许不同（合规差异），口径外字段待确认。
    fields = (set(entry_a) | set(entry_b)) - set(CRITICAL_FIELDS)
    for field in sorted(fields):
        raw_a = entry_a.get(field, _ABSENT)
        raw_b = entry_b.get(field, _ABSENT)
        if raw_a is not _ABSENT and raw_b is not _ABSENT:
            if normalize_value(field, raw_a) == normalize_value(field, raw_b):
                continue
        is_absent_a = raw_a is _ABSENT
        is_absent_b = raw_b is _ABSENT
        if field in INFORMATIONAL_FIELDS:
            verdict = VERDICT_COMPLIANT
            reason = "信息字段 %s 不一致，属允许不同的描述性元数据" % field
            bucket = compliant_diffs
        else:
            verdict = VERDICT_PENDING
            reason = "字段 %s 不在比对口径内，需人工确认" % field
            bucket = pending
        if is_absent_a or is_absent_b:
            reason += "（%s）" % (
                "a 侧字段缺失" if is_absent_a else "b 侧字段缺失")
        bucket.append(_field_mismatch_record(
            key, field,
            None if is_absent_a else raw_a,
            None if is_absent_b else raw_b,
            verdict, reason))


def compare_manifests(manifest_a, manifest_b):
    """比对两份资源清单。

    manifest_a 为基准（申报/审批）侧，manifest_b 为对方（实际）侧。
    返回带 summary 与四类差异清单的结果字典。
    """
    resources_a = _extract_resources(manifest_a)
    resources_b = _extract_resources(manifest_b)

    violations = []
    compliant_diffs = []
    pending_confirmations = []
    missing_critical_fields = []

    index_a = _index_entries(resources_a, "a", missing_critical_fields,
                             pending_confirmations)
    index_b = _index_entries(resources_b, "b", missing_critical_fields,
                             pending_confirmations)

    matched_entries = 0
    for key, (_pos, entry_a) in index_a.items():
        if key not in index_b:
            violations.append({
                "category": CAT_ENTRY_MISSING,
                "verdict": VERDICT_VIOLATION,
                "resource": {"name": key[0], "type": key[1]},
                "field": None,
                "entry_a": entry_a,
                "entry_b": None,
                "reason": "基准清单存在而对方清单缺失（应部署未部署）",
            })
            continue
        matched_entries += 1
        _pos_b, entry_b = index_b[key]
        _compare_entry(key, entry_a, entry_b, violations, compliant_diffs,
                       pending_confirmations, missing_critical_fields)

    for key, (_pos, entry_b) in index_b.items():
        if key not in index_a:
            violations.append({
                "category": CAT_ENTRY_EXTRA,
                "verdict": VERDICT_VIOLATION,
                "resource": {"name": key[0], "type": key[1]},
                "field": None,
                "entry_a": None,
                "entry_b": entry_b,
                "reason": "对方清单存在而基准清单没有（未申报/多余资源）",
            })

    if violations or missing_critical_fields:
        verdict = "不合规"
    elif pending_confirmations:
        verdict = "待确认"
    elif compliant_diffs:
        verdict = "合规（存在合规差异）"
    else:
        verdict = "完全一致"

    summary = {
        "entries_a": len(resources_a),
        "entries_b": len(resources_b),
        "matched_entries": matched_entries,
        "violation_count": len(violations),
        "compliant_diff_count": len(compliant_diffs),
        "pending_count": len(pending_confirmations),
        "missing_critical_field_count": len(missing_critical_fields),
        "verdict": verdict,
    }

    return {
        "summary": summary,
        "violations": violations,
        "compliant_diffs": compliant_diffs,
        "pending_confirmations": pending_confirmations,
        "missing_critical_fields": missing_critical_fields,
    }


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="资源清单差异比对与合规判定（基准侧 vs 实际侧）")
    parser.add_argument("baseline", help="基准（申报/审批）侧清单 JSON")
    parser.add_argument("actual", help="对方（实际）侧清单 JSON")
    parser.add_argument("-o", "--output", help="结果 JSON 输出路径，缺省打印到标准输出")
    args = parser.parse_args(argv)

    with open(args.baseline, "r", encoding="utf-8") as fh:
        manifest_a = json.load(fh)
    with open(args.actual, "r", encoding="utf-8") as fh:
        manifest_b = json.load(fh)

    result = compare_manifests(manifest_a, manifest_b)
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=False)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    else:
        print(text)

    # 退出码：0 = 完全一致/仅有合规差异，1 = 不合规，3 = 待确认
    verdict = result["summary"]["verdict"]
    if verdict == "不合规":
        return 1
    if verdict == "待确认":
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
