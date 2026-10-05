"""报告渲染（文本 / dict，均对敏感参数脱敏）。"""
from __future__ import annotations

from .attribution import MISSING
from .engine import CATEGORY_LABELS, CATEGORY_ORDER, Report


def _display(value, sensitive: bool, fingerprint: str | None) -> str:
    if value is MISSING:
        return "<缺失>"
    if sensitive:
        return f"****** (sha256:{fingerprint})" if fingerprint else "******"
    text = str(value)
    return text if len(text) <= 80 else text[:77] + "..."


def _render_item(item, label_a: str, label_b: str) -> str:
    va = _display(item.value_a, item.sensitive, item.fingerprint_a)
    vb = _display(item.value_b, item.sensitive, item.fingerprint_b)
    line = f"  - {item.key}\n      {label_a} = {va}\n      {label_b} = {vb}\n"
    line += f"      归因：{item.attribution} | 依据：{item.reason}"
    return line


def render_text(report: Report) -> str:
    lines = [
        "=" * 68,
        f"环境参数差异对比：{report.label_a}  vs  {report.label_b}",
        "=" * 68,
        f"完全一致：{report.identical_count}",
        f"写法不同但等价（非差异）：{len(report.equivalent)}",
    ]
    for category in CATEGORY_ORDER:
        lines.append(f"{CATEGORY_LABELS[category]}：{len(report.by_category(category))}")
    lines.append(f"需处理的问题项（待确认 + 疑似遗漏）：{report.problem_count}")

    if report.equivalent:
        lines.append("")
        lines.append("-- 值等价、写法不同（不作为差异） --")
        for item in report.equivalent:
            va = _display(item.value_a, item.sensitive, item.fingerprint_a)
            vb = _display(item.value_b, item.sensitive, item.fingerprint_b)
            lines.append(f"  - {item.key}  [{ '、'.join(item.reasons) }]")
            lines.append(f"      {report.label_a} = {va}")
            lines.append(f"      {report.label_b} = {vb}")

    for category in CATEGORY_ORDER:
        items = report.by_category(category)
        if not items:
            continue
        lines.append("")
        lines.append(f"-- {CATEGORY_LABELS[category]}（{category}）--")
        for item in items:
            lines.append(_render_item(item, report.label_a, report.label_b))

    return "\n".join(lines)


def report_to_dict(report: Report) -> dict:
    def value_pair(item):
        return {
            "value_a": _display(item.value_a, item.sensitive, item.fingerprint_a),
            "value_b": _display(item.value_b, item.sensitive, item.fingerprint_b),
        }

    return {
        "label_a": report.label_a,
        "label_b": report.label_b,
        "identical_count": report.identical_count,
        "equivalent": [
            {
                "key": item.key,
                "reasons": item.reasons,
                "sensitive": item.sensitive,
                "fingerprint_a": item.fingerprint_a,
                "fingerprint_b": item.fingerprint_b,
                **value_pair(item),
            }
            for item in report.equivalent
        ],
        "diffs": [
            {
                "key": item.key,
                "category": item.category,
                "category_label": CATEGORY_LABELS[item.category],
                "attribution": item.attribution,
                "reason": item.reason,
                "sensitive": item.sensitive,
                "fingerprint_a": item.fingerprint_a,
                "fingerprint_b": item.fingerprint_b,
                **value_pair(item),
            }
            for item in report.diffs
        ],
        "problem_count": report.problem_count,
    }
