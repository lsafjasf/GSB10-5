"""检查报告渲染：JSON 与 Markdown 两种格式。"""

import json

__all__ = ["to_json", "to_markdown"]


def to_json(report):
    return json.dumps(report.to_dict(), ensure_ascii=False, indent=2)


def _pos(d):
    if "line" in d:
        return "%s:%d:%d" % (d.get("segment", "?"), d.get("line", 0),
                             d.get("col", 0))
    return d.get("segment", "?")


def to_markdown(report):
    d = report.to_dict()
    out = ["# 译名一致性检查报告", ""]
    s = d["stats"]
    out.append("## 概览")
    out.append("")
    out.append("- 语料段数：%(segments)d，术语条目：%(entries)d，"
               "登记译名：%(renders)d" % s)
    out.append("- 源文命中：%(source_matches)d，译文命中：%(target_matches)d，"
               "耗时：%(elapsed_sec)s s" % s)
    out.append("- 冲突：%d 条；待人工确认：%d 条；术语库问题：%d 条"
               % (len(d["conflicts"]), len(d["review"]),
                  len(d["termbase_issues"])))
    out.append("")

    out.append("## 冲突清单")
    out.append("")
    if not d["conflicts"]:
        out.append("（无）")
    for i, c in enumerate(d["conflicts"], 1):
        if c["type"] == "multiple_renderings":
            out.append("%d. **[multiple_renderings]** 条目 `%s`（%s）"
                       % (i, c["entry_id"], c["message"]))
            for occ in c["renderings"]:
                out.append("   - `%s`（%s）首见于 %s" %
                           (occ["render"], occ["kind"], _pos(occ)))
        else:
            out.append("%d. **[%s]** 条目 `%s` @ %s — %s"
                       % (i, c["type"], c["entry_id"], _pos(c),
                          c["message"]))
    out.append("")

    out.append("## 待人工确认")
    out.append("")
    if not d["review"]:
        out.append("（无）")
    for i, r in enumerate(d["review"], 1):
        out.append("%d. **[%s]** 条目 `%s` @ %s — %s"
                   % (i, r["type"], r["entry_id"], _pos(r), r["message"]))
    out.append("")

    if d["termbase_issues"]:
        out.append("## 术语库问题")
        out.append("")
        for i, t in enumerate(d["termbase_issues"], 1):
            out.append("%d. **[%s]** 条目 `%s` — %s"
                       % (i, t["type"], t["entry_id"], t["message"]))
        out.append("")
    return "\n".join(out)
