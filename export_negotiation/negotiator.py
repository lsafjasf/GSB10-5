"""数据导出格式协商。

协商顺序（依据见 docs/NEGOTIATION_RULES.md）：
1. 精确匹配：请求 id 或其规范别名在注册表中 -> 直接命中；
2. 降级：请求格式已知但不支持，按“同族”找首选替代格式；
3. 部分满足：上述候选只能覆盖部分字段时，返回部分成功 + 缺失字段；
4. 完全失败：所有候选覆盖率为 0 时返回 unsupported，并附可用选项列表。

所有输出均为可 JSON 序列化的纯 dict/list，且不含集合迭代，
同一请求重复调用结果逐字节一致。
"""

from .registry import ALIASES, FALLBACK_FAMILY, FORMATS

# 协商状态
EXACT = "exact"                # 精确命中（含别名）且字段全部可导出
DOWNGRADED = "downgraded"      # 降级到同族替代格式，字段全部可导出
PARTIAL = "partial"            # 命中/降级成功但部分字段无法导出
UNSUPPORTED = "unsupported"    # 没有任何可用格式

# 请求中允许出现的字段类型（注册表支持类型的并集 + 已知但无人支持的 binary）
KNOWN_FIELD_TYPES = set()
for spec in FORMATS.values():
    KNOWN_FIELD_TYPES.update(spec["field_types"])
KNOWN_FIELD_TYPES.add("binary")


def _family_candidates(family):
    """族内候选，按 preference 升序、id 字典序排序（确定性依据）。"""
    rows = [
        (spec["preference"], fmt_id, spec)
        for fmt_id, spec in FORMATS.items()
        if spec["family"] == family
    ]
    rows.sort(key=lambda row: (row[0], row[1]))
    return rows


def _coverage(spec, field_types):
    """返回（可导出字段下标列表, 缺失下标列表），保持请求顺序。"""
    supported = spec["field_types"]
    ok, missing = [], []
    for idx, ftype in enumerate(field_types):
        (ok if ftype in supported else missing).append(idx)
    return ok, missing


def _option_record(fmt_id, spec, fields, field_types):
    ok_idx, missing_idx = _coverage(spec, field_types)
    total = len(fields)
    coverage = (len(ok_idx) / total) if total else 1.0
    return {
        "format": fmt_id,
        "family": spec["family"],
        "preference": spec["preference"],
        "coverage": coverage,
        "supported_field_types": sorted(spec["field_types"]),
        "exportable_fields": [fields[i][0] for i in ok_idx],
        "missing_fields": [
            {
                "name": fields[i][0],
                "type": field_types[i],
                "reason": "格式 %s 不支持字段类型 %s" % (fmt_id, field_types[i]),
            }
            for i in missing_idx
        ],
        "note": spec["note"],
        "reason": _option_reason(fmt_id, spec, coverage, missing_idx, fields),
    }


def _option_reason(fmt_id, spec, coverage, missing_idx, fields):
    if coverage == 1.0:
        return "%s 族首选格式 %s，可无损导出全部字段" % (spec["family"], fmt_id)
    names = ", ".join(fields[i][0] for i in missing_idx)
    return "%s 族格式 %s 仅覆盖 %.0f%% 字段，缺失：%s" % (
        spec["family"], fmt_id, coverage * 100, names
    )


def _all_options(fields, field_types):
    options = [
        _option_record(fmt_id, spec, fields, field_types)
        for fmt_id, spec in FORMATS.items()
    ]
    # 确定性排序：覆盖率降序 -> 族内 preference 升序 -> id 字典序
    options.sort(key=lambda opt: (-opt["coverage"], opt["preference"], opt["format"]))
    return options


def negotiate(requested_format, fields):
    """协商导出格式。

    参数:
        requested_format: 请求的格式 id（大小写敏感），如 "xlsx"。
        fields: 字段列表，元素为 (字段名, 字段类型) 二元组，顺序即列顺序。

    返回:
        dict，键包括 status / requested / format / family / reason /
        exportable_fields / missing_fields / options / trace。

    异常:
        ValueError: 请求字段类型不在已知类型集合内（属输入错误，不是协商失败）。
    """
    if not isinstance(requested_format, str) or not requested_format:
        raise ValueError("requested_format 必须是非空字符串")

    normalized_fields = []
    for field in fields:
        if len(field) != 2:
            raise ValueError("字段必须是 (name, type) 二元组: %r" % (field,))
        name, ftype = field
        if ftype not in KNOWN_FIELD_TYPES:
            raise ValueError("未知字段类型 %r（字段 %s）" % (ftype, name))
        normalized_fields.append((name, ftype))

    field_types = [ftype for _, ftype in normalized_fields]
    trace = []

    # ---- 1. 精确匹配（含别名规范化）----
    canonical = ALIASES.get(requested_format, requested_format)
    if canonical in FORMATS:
        spec = FORMATS[canonical]
        trace.append(
            "精确匹配：%s%s 在注册表中命中"
            % (requested_format, "" if canonical == requested_format else " 规范化为 " + canonical)
        )
        ok_idx, missing_idx = _coverage(spec, field_types)
        result = _base_result(requested_format, normalized_fields)
        result["format"] = canonical
        result["family"] = spec["family"]
        result["exportable_fields"] = [normalized_fields[i][0] for i in ok_idx]
        result["missing_fields"] = [
            {
                "name": normalized_fields[i][0],
                "type": field_types[i],
                "reason": "格式 %s 不支持字段类型 %s" % (canonical, field_types[i]),
            }
            for i in missing_idx
        ]
        if missing_idx:
            result["status"] = PARTIAL
            result["reason"] = (
                "格式 %s 精确命中，但 %d/%d 个字段无法导出"
                % (canonical, len(missing_idx), len(normalized_fields))
            )
            trace.append("字段覆盖：%d 可导出，%d 缺失 -> partial"
                         % (len(ok_idx), len(missing_idx)))
        else:
            result["status"] = EXACT
            result["reason"] = "格式 %s 精确命中，全部字段可无损导出" % canonical
            trace.append("字段覆盖：全部可导出 -> exact")
        result["options"] = _all_options(normalized_fields, field_types)
        result["trace"] = trace
        return result

    # ---- 2. 降级：已知格式但不支持，限定同族替代 ----
    options = _all_options(normalized_fields, field_types)
    result = _base_result(requested_format, normalized_fields)
    result["options"] = options
    family = FALLBACK_FAMILY.get(requested_format)
    if family is not None:
        trace.append("格式 %s 已知但不受支持，允许在同族 %s 内降级"
                     % (requested_format, family))
        family_options = [opt for opt in options if opt["family"] == family]
        best = family_options[0]  # options 已按确定性规则排序
        trace.append("族内首选：%s（coverage=%.2f）" % (best["format"], best["coverage"]))
        if best["coverage"] == 0.0:
            trace.append("族内最优覆盖率为 0 -> unsupported")
            return _finish_unsupported(result, normalized_fields, trace)
        return _finish_with_candidate(result, best, DOWNGRADED, trace,
                                      "按同族（%s）首选降级" % family)

    # ---- 3. 未知格式：在全部选项中选覆盖最优者 ----
    trace.append("格式 %s 未知，按覆盖率/首选度对全部可用格式排序" % requested_format)
    best = options[0]
    if best["coverage"] == 0.0:
        trace.append("最优选项 %s 覆盖率为 0 -> unsupported" % best["format"])
        return _finish_unsupported(result, normalized_fields, trace)

    trace.append("最优选项：%s（coverage=%.2f）" % (best["format"], best["coverage"]))
    return _finish_with_candidate(result, best, DOWNGRADED, trace,
                                  "请求格式未知，选用全局覆盖最优格式")


def _base_result(requested_format, fields):
    return {
        "status": None,
        "requested": requested_format,
        "format": None,
        "family": None,
        "reason": None,
        "exportable_fields": [],
        "missing_fields": [],
        "options": [],
        "trace": [],
    }


def _finish_unsupported(result, fields, trace):
    result["status"] = UNSUPPORTED
    result["format"] = None
    result["family"] = None
    result["exportable_fields"] = []
    result["missing_fields"] = [
        {
            "name": name,
            "type": ftype,
            "reason": "无任何已注册格式支持字段类型 %s" % ftype,
        }
        for name, ftype in fields
    ]
    result["reason"] = "没有任何可用格式能导出请求字段"
    result["trace"] = trace
    return result

def _finish_with_candidate(result, option, full_status, trace, why):
    result["format"] = option["format"]
    result["family"] = option["family"]
    result["exportable_fields"] = list(option["exportable_fields"])
    result["missing_fields"] = [dict(item) for item in option["missing_fields"]]
    if option["missing_fields"]:
        result["status"] = PARTIAL
        result["reason"] = (
            "%s，%s 只能导出 %d/%d 个字段"
            % (why, option["format"],
               len(option["exportable_fields"]),
               len(option["exportable_fields"]) + len(option["missing_fields"]))
        )
        trace.append("字段覆盖不全 -> partial")
    else:
        result["status"] = full_status
        result["reason"] = "%s -> %s，全部字段可导出" % (why, option["format"])
        trace.append("字段全部可导出 -> %s" % full_status)
    result["trace"] = trace
    return result
