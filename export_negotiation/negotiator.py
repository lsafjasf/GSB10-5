"""导出格式协商核心。

协商顺序（详见 RULES.md）：
  1. 规范化请求（格式名小写去空白、别名解析、字段去重排序）
  2. 精确匹配：请求的格式已注册 -> 检查字段覆盖
       全覆盖 -> exact；部分覆盖 -> partial（附缺失字段清单）
  3. 降级：部分覆盖（或格式未注册）-> 沿降级链逐环尝试（R5）
  4. 族内回退：降级链无果 -> 按"同类型、优先级降序、名称升序"取第一个（R6）
  5. 失败：以上皆空 -> failed，返回全部可用选项及选择理由（R7）

确定性（R8）：所有排序键显式给出，不依赖哈希序；同一请求重复协商结果一致。

原则：数据完整性优先于格式偏好 —— 请求格式会丢字段时，优先降级到
能全覆盖的格式；仅当没有任何格式能全覆盖时才返回部分成功（partial）。
"""

from .registry import FORMATS, ALIASES

EXACT = "exact"
DOWNGRADED = "downgraded"
PARTIAL = "partial"
FAILED = "failed"

# 字段类型不支持时的缺失原因（固定文案，保证确定性）
_MISSING_REASON = {
    "nested": "目标格式为扁平结构，无法表达嵌套对象",
    "binary": "目标格式为纯文本，无法嵌入二进制内容",
    "richtext": "目标格式无标记能力，富文本将丢失格式",
    "date": "目标格式无日期类型，日期将退化为字符串",
}


def _normalize_format(name):
    """R1：小写、去空白、别名解析。"""
    key = str(name).strip().lower()
    return ALIASES.get(key, key)


def _normalize_fields(fields):
    """R1：字段去重并按名称排序，保证与输入顺序无关。"""
    seen = {}
    for f in fields or []:
        seen.setdefault(f["name"], f["type"])
    return tuple(sorted(seen.items()))  # tuple[(name, type), ...]


def _coverage(fmt, fields):
    """计算 fmt 对 fields 的覆盖情况，返回 (supported, missing)。

    missing: tuple[(field_name, field_type, reason), ...]，按字段名升序。
    """
    caps = FORMATS[fmt]["field_types"]
    supported, missing = [], []
    for name, ftype in fields:
        if ftype in caps:
            supported.append(name)
        else:
            reason = _MISSING_REASON.get(ftype, "目标格式不支持该字段类型")
            missing.append((name, ftype, reason))
    return tuple(supported), tuple(missing)


def _fallback_chain(fmt):
    """R5：从 FORMATS[fmt]['fallbacks'] 开始的降级链（fmt 未注册时为空）。"""
    chain, seen = [], set()
    cur = FORMATS.get(fmt, {}).get("fallbacks", [])
    for nxt in cur:
        while nxt and nxt not in seen:
            seen.add(nxt)
            chain.append(nxt)
            nxt = FORMATS.get(nxt, {}).get("fallbacks", [])
            nxt = nxt[0] if nxt else None
    return chain


def _family_fallback(fmt):
    """R6：同 kind 族内回退，键 = (-priority, name) 取最小。"""
    spec = FORMATS.get(fmt)
    if spec is None:
        return []
    kind = spec["kind"]
    cands = sorted(
        (name for name, s in FORMATS.items() if s["kind"] == kind and name != fmt),
        key=lambda n: (-FORMATS[n]["priority"], n),
    )
    return cands[:1]


def _option_entry(fmt, fields):
    """可用选项条目：格式数据 + 覆盖情况 + 选择理由。"""
    spec = FORMATS[fmt]
    supported, missing = _coverage(fmt, fields)
    return {
        "format": fmt,
        "kind": spec["kind"],
        "priority": spec["priority"],
        "reason": spec["reason"],
        "covers_all_fields": not missing,
        "supported_fields": list(supported),
        "missing_fields": [
            {"name": n, "type": t, "reason": r} for n, t, r in missing
        ],
    }


def _available_options(fields):
    """R7：全部可用选项，按 (-priority, name) 排序，附选择理由。"""
    ordered = sorted(FORMATS, key=lambda n: (-FORMATS[n]["priority"], n))
    return [_option_entry(fmt, fields) for fmt in ordered]


def _result(status, requested, fields, **kw):
    out = {
        "status": status,
        "requested_format": requested,
        "selected_format": None,
        "exported_fields": list(dict(fields)),
        "missing_fields": [],
        "notes": [],
    }
    out.update(kw)
    return out


def negotiate(requested_format, fields):
    """协商入口。

    requested_format: 用户请求的格式名（str）
    fields: [{"name": str, "type": str}, ...] 数据模型的字段清单
    返回 dict，status ∈ {exact, downgraded, partial, failed}。
    """
    requested = _normalize_format(requested_format)
    fields = _normalize_fields(fields)

    # 步骤 2：精确匹配
    if requested in FORMATS:
        supported, missing = _coverage(requested, fields)
        if not missing:
            return _result(
                EXACT, requested, fields,
                selected_format=requested,
                notes=["请求格式精确命中，全部字段可导出"],
            )
        # 步骤 3：请求格式会丢字段，先尝试降级到全覆盖格式（R3）
        for cand in _fallback_chain(requested) + _family_fallback(requested):
            if cand in FORMATS and not _coverage(cand, fields)[1]:
                return _result(
                    DOWNGRADED, requested, fields,
                    selected_format=cand,
                    notes=[
                        "请求格式 '%s' 无法导出 %d 个字段，已降级为 '%s' 以完整导出"
                        % (requested, len(missing), cand),
                        "选择依据：%s" % FORMATS[cand]["reason"],
                    ],
                    requested_missing_fields=[
                        {"name": n, "type": t, "reason": r} for n, t, r in missing
                    ],
                )
        # 步骤 5a：无任何格式能全覆盖 -> 部分成功，报告缺失清单（R4）
        full_alternatives = [
            fmt for fmt in sorted(FORMATS, key=lambda n: (-FORMATS[n]["priority"], n))
            if fmt != requested and not _coverage(fmt, fields)[1]
        ]
        return _result(
            PARTIAL, requested, fields,
            selected_format=requested,
            exported_fields=list(supported),
            missing_fields=[
                {"name": n, "type": t, "reason": r} for n, t, r in missing
            ],
            notes=[
                "请求格式支持，但 %d 个字段无法导出，已部分导出" % len(missing),
            ],
            full_support_alternatives=full_alternatives,
        )

    # 步骤 3+4：请求格式未注册 -> 降级链 -> 族内回退
    chain = _fallback_chain(requested) + _family_fallback(requested)
    for cand in chain:
        if cand not in FORMATS:
            continue
        supported, missing = _coverage(cand, fields)
        if missing:
            continue
        return _result(
            DOWNGRADED, requested, fields,
            selected_format=cand,
            notes=[
                "请求格式 '%s' 不受支持，已按降级链选择 '%s'" % (requested, cand),
                "选择依据：%s" % FORMATS[cand]["reason"],
            ],
        )

    # 步骤 5：失败，返回可用选项（R7）
    return _result(
        FAILED, requested, fields,
        notes=["请求格式 '%s' 不受支持且无可用降级，请从可用选项中选择" % requested],
        available_options=_available_options(fields),
    )
