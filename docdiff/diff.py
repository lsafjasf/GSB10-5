"""差异归类、摘要/明细双向还原、阈值扫描。

归类规则（详见 README.md）:
- 先匹配后归类：所有“修改型”变化都建立在同一章节的配对上，绝不把
  改名/移动拆成“删除+新增”。
- 对每个已配对章节按以下互斥优先级归入主类:
    1. reparent     父章节配对关系发生变化（父不同）
    2. level_change 标题级别变化（父相同）
    3. rename       标题文本变化（父相同、级别相同）
    4. modified     仅正文变化（content_jaccard < 1）
    5. unchanged    以上皆无
- 附加标志（可叠加，不改变主类）: renamed / moved / level_changed /
  content_modified / reordered（配对兄弟相对位次是否变化）/
  subtree_changed（直接子章节的配对集合是否变化，用于发现子树层面的
  增删与搬出/搬入）。
- 未配对章节: added / deleted。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from .match import (CONTENT_WEIGHT, DEFAULT_THRESHOLD, TITLE_WEIGHT,
                    Candidate, Matcher)
from .model import SectionIndex, parse_document

SCHEMA_VERSION = 1
CATEGORY_LABELS = {
    "added": "新增章节",
    "deleted": "删除章节",
    "reparent": "移动（父章节变化）",
    "level_change": "层级调整",
    "rename": "改名",
    "modified": "内容修改",
    "unchanged": "未变化",
}


@dataclass
class Change:
    category: str
    old_path: Optional[str]
    new_path: Optional[str]
    old_title: Optional[str]
    new_title: Optional[str]
    old_level: Optional[int]
    new_level: Optional[int]
    old_parent_path: Optional[str]
    new_parent_path: Optional[str]
    content_jaccard: Optional[float]
    title_sim: Optional[float]
    score: Optional[float]
    flags: List[str] = field(default_factory=list)
    reason: str = ""
    old_ordinal: Optional[int] = None
    new_ordinal: Optional[int] = None
    old_id: Optional[int] = None
    new_id: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Change":
        return Change(**d)


@dataclass
class DiffResult:
    threshold: float
    old_total_lines: int
    new_total_lines: int
    old_section_count: int
    new_section_count: int
    matched_pairs: int
    changes: List[Change]

    def stats(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for c in self.changes:
            out[c.category] = out.get(c.category, 0) + 1
        return out

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "threshold": self.threshold,
            "weights": {"content": CONTENT_WEIGHT, "title": TITLE_WEIGHT},
            "old_total_lines": self.old_total_lines,
            "new_total_lines": self.new_total_lines,
            "old_section_count": self.old_section_count,
            "new_section_count": self.new_section_count,
            "matched_pairs": self.matched_pairs,
            "stats": self.stats(),
            "changes": [c.to_dict() for c in self.changes],
        }


# ---------------------------------------------------------------- 归类

def _mapped_sibling_position(sec, mapping, other_sections) -> int:
    """该章节在父节点下、仅计“两侧都存在且成功配对”的兄弟时的相对位次。

    用配对兄弟的相对位次判断顺序变化，可避免兄弟被增删造成的误报。
    """
    pos = 0
    for sib in sec.parent.children:
        if sib.id == sec.id:
            break
        mapped = mapping.get(sib.id)
        if mapped is not None and other_sections[mapped].parent is not None:
            pos += 1
    return pos


def _invert(mapping: Dict[int, int]) -> Dict[int, int]:
    return {v: k for k, v in mapping.items()}


def _classify(old: SectionIndex, new: SectionIndex,
              mapping: Dict[int, int],
              candidates: List[Candidate]) -> List[Change]:
    cand_by_pair = {(c.old_id, c.new_id): c for c in candidates}
    changes: List[Change] = []
    mapped_new = set(mapping.values())

    for osec in old.sections:
        if osec.id == 0:
            continue
        nid = mapping.get(osec.id)
        if nid is None:
            changes.append(Change(
                category="deleted",
                old_path=osec.path(), new_path=None,
                old_title=osec.title, new_title=None,
                old_level=osec.level, new_level=None,
                old_parent_path=osec.parent.path() if osec.parent and osec.parent.id else "",
                new_parent_path=None,
                content_jaccard=None, title_sim=None, score=None,
                reason="旧版章节在新版中无任何达到阈值的配对",
                old_ordinal=osec.ordinal, old_id=osec.id))
            continue

        nsec = new.sections[nid]
        cand = cand_by_pair.get((osec.id, nid))
        jaccard = cand.content_jaccard if cand else 1.0
        tsim = cand.title_sim if cand else (1.0 if osec.title == nsec.title else 0.0)
        score = cand.score if cand else 1.0
        inferred = bool(cand and cand.inferred)

        oparent_mapped = mapping.get(osec.parent.id) if osec.parent else None
        reparented = oparent_mapped != nsec.parent.id
        level_changed = osec.level != nsec.level
        renamed = osec.title != nsec.title
        # 仅当章节确有自身正文时，内容差异才成立（空正文容器的 jaccard=0 是
        # 指纹缺失而非内容被改）。
        content_modified = (jaccard < 1.0
                            and bool(osec.own_shingles or nsec.own_shingles))

        o_pos = _mapped_sibling_position(osec, mapping, new.sections)
        n_pos = _mapped_sibling_position(nsec, _invert(mapping), old.sections)
        reordered = (not reparented) and o_pos != n_pos

        mapped_new_ids = set(mapping.values())
        old_child_side = {mapping.get(c.id) for c in osec.children}
        new_child_side = {c.id if c.id in mapped_new_ids else None
                          for c in nsec.children}
        subtree_changed = old_child_side != new_child_side

        flags = []
        if renamed:
            flags.append("renamed")
        if reparented:
            flags.append("moved")
        if level_changed:
            flags.append("level_changed")
        if content_modified:
            flags.append("content_modified")
        if reordered:
            flags.append("reordered")
        if subtree_changed:
            flags.append("subtree_changed")

        if reparented:
            category = "reparent"
        elif level_changed:
            category = "level_change"
        elif renamed:
            category = "rename"
        elif content_modified:
            category = "modified"
        else:
            category = "unchanged"

        if cand is None:
            basis = "锚点 {#id} 相同, 强制判定为同一章节"
        elif inferred:
            basis = (f"自身正文指纹过少(<{8}个), 由已配对子章节的多数派父章节"
                     f"推断为同一章节(标题相似度 {tsim:.3f})")
        else:
            basis = (f"score={score:.3f}=0.8*内容Jaccard({jaccard:.3f})"
                     f"+0.2*标题相似度({tsim:.3f}) >= 阈值, 判定为同一章节")
        reasons = {
            "reparent": f"父章节由「{osec.parent.path() or '<root>'}」变为"
                        f"「{nsec.parent.path() or '<root>'}」",
            "level_change": f"标题级别由 H{osec.level} 变为 H{nsec.level}",
            "rename": f"标题由「{osec.title}」改为「{nsec.title}」",
            "modified": f"正文内容 Jaccard={jaccard:.3f} < 1",
            "unchanged": "标题、层级、父章节、正文均未变化",
        }
        changes.append(Change(
            category=category,
            old_path=osec.path(), new_path=nsec.path(),
            old_title=osec.title, new_title=nsec.title,
            old_level=osec.level, new_level=nsec.level,
            old_parent_path=osec.parent.path() if osec.parent and osec.parent.id else "",
            new_parent_path=nsec.parent.path() if nsec.parent and nsec.parent.id else "",
            content_jaccard=round(jaccard, 6),
            title_sim=round(tsim, 6),
            score=round(score, 6),
            flags=flags,
            reason=basis + "; " + reasons[category],
            old_ordinal=osec.ordinal, new_ordinal=nsec.ordinal,
            old_id=osec.id, new_id=nsec.id))

    for nsec in new.sections:
        if nsec.id == 0 or nsec.id in mapped_new:
            continue
        changes.append(Change(
            category="added",
            old_path=None, new_path=nsec.path(),
            old_title=None, new_title=nsec.title,
            old_level=None, new_level=nsec.level,
            old_parent_path=None,
            new_parent_path=nsec.parent.path() if nsec.parent and nsec.parent.id else "",
            content_jaccard=None, title_sim=None, score=None,
            reason="新版章节在旧版中无任何达到阈值的配对",
            new_ordinal=nsec.ordinal, new_id=nsec.id))

    changes.sort(key=lambda c: (c.old_id if c.old_id is not None else 10 ** 9,
                                c.new_id if c.new_id is not None else 10 ** 9))
    return changes


def diff_documents(old_text: str, new_text: str,
                   threshold: float = DEFAULT_THRESHOLD) -> DiffResult:
    old = parse_document(old_text)
    new = parse_document(new_text)
    matcher = Matcher(threshold=threshold)
    result = matcher.match(old, new)
    changes = _classify(old, new, result.mapping, result.candidates)
    return DiffResult(
        threshold=threshold,
        old_total_lines=old.total_lines,
        new_total_lines=new.total_lines,
        old_section_count=len(old.sections) - 1,
        new_section_count=len(new.sections) - 1,
        matched_pairs=len(result.mapping) - 1,
        changes=changes)


def diff_files(old_path: str, new_path: str,
               threshold: float = DEFAULT_THRESHOLD) -> DiffResult:
    with open(old_path, encoding="utf-8") as f:
        old_text = f.read()
    with open(new_path, encoding="utf-8") as f:
        new_text = f.read()
    return diff_documents(old_text, new_text, threshold)


# ------------------------------------------------------- 摘要 <-> 明细

def summarize(diff: DiffResult) -> dict:
    """明细 -> 摘要。manifest 逐条对应 changes（含定位信息），可无损展开。"""
    manifest = []
    for c in diff.changes:
        manifest.append({
            "category": c.category,
            "old_id": c.old_id, "new_id": c.new_id,
            "old_path": c.old_path, "new_path": c.new_path,
            "old_title": c.old_title, "new_title": c.new_title,
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "threshold": diff.threshold,
        "weights": {"content": CONTENT_WEIGHT, "title": TITLE_WEIGHT},
        "old_total_lines": diff.old_total_lines,
        "new_total_lines": diff.new_total_lines,
        "old_section_count": diff.old_section_count,
        "new_section_count": diff.new_section_count,
        "matched_pairs": diff.matched_pairs,
        "stats": diff.stats(),
        "manifest": manifest,
    }


def expand_summary(summary: dict) -> List[Change]:
    """摘要 -> 明细骨架（每条 Change 的定位/类别字段与原始明细一致）。"""
    out = []
    for m in summary["manifest"]:
        out.append(Change(
            category=m["category"],
            old_path=m["old_path"], new_path=m["new_path"],
            old_title=m["old_title"], new_title=m["new_title"],
            old_level=None, new_level=None,
            old_parent_path=None, new_parent_path=None,
            content_jaccard=None, title_sim=None, score=None,
            old_id=m["old_id"], new_id=m["new_id"]))
    return out


def _skeleton(c: Change) -> tuple:
    return (c.category, c.old_id, c.new_id, c.old_path, c.new_path,
            c.old_title, c.new_title)


def verify_roundtrip(diff: DiffResult) -> dict:
    """验证 摘要<->明细 双向还原。

    正向: summarize 的 manifest 与 changes 一一对应（数量、顺序、类别、定位）。
    反向: expand_summary(summarize(diff)) 的每条记录与原始 Change 的骨架
          (类别/两侧id/两侧路径/两侧标题) 完全相等；且两次 summarize 结果一致。
    """
    summary1 = summarize(diff)
    expanded = expand_summary(summary1)
    summary2 = summarize(DiffResult(
        threshold=diff.threshold,
        old_total_lines=diff.old_total_lines,
        new_total_lines=diff.new_total_lines,
        old_section_count=diff.old_section_count,
        new_section_count=diff.new_section_count,
        matched_pairs=diff.matched_pairs,
        changes=expanded))
    forward_ok = (len(summary1["manifest"]) == len(diff.changes)
                  and summary1["stats"] == diff.stats())
    backward_ok = ([_skeleton(c) for c in expanded]
                   == [_skeleton(c) for c in diff.changes])
    resummary_ok = summary1 == summary2
    return {
        "forward_ok": forward_ok,
        "backward_ok": backward_ok,
        "resummary_ok": resummary_ok,
        "ok": forward_ok and backward_ok and resummary_ok,
        "change_count": len(diff.changes),
        "manifest_count": len(summary1["manifest"]),
        "expanded_count": len(expanded),
        "sample_manifest_entry": summary1["manifest"][0] if summary1["manifest"] else None,
    }


# ---------------------------------------------------------- 阈值扫描

def sweep_thresholds(old_text: str, new_text: str,
                     thresholds: List[float]) -> List[dict]:
    """对每个阈值重新匹配并归类，返回各阈值的统计与逐章节归类。"""
    old = parse_document(old_text)
    new = parse_document(new_text)
    rows = []
    for t in thresholds:
        matcher = Matcher(threshold=t)
        result = matcher.match(old, new)
        changes = _classify(old, new, result.mapping, result.candidates)
        stats: Dict[str, int] = {}
        for c in changes:
            stats[c.category] = stats.get(c.category, 0) + 1
        rows.append({
            "threshold": t,
            "matched_pairs": len(result.mapping) - 1,
            "stats": stats,
            "assignments": [
                {"old_path": c.old_path, "new_path": c.new_path,
                 "category": c.category, "score": c.score}
                for c in changes if c.category not in ("unchanged",)
            ],
        })
    return rows


# ---------------------------------------------------------- 渲染

def render_markdown(diff: DiffResult) -> str:
    lines = ["# 文档版本差异报告", ""]
    lines.append(f"- 阈值: {diff.threshold}（score = 0.8*内容Jaccard + 0.2*标题相似度）")
    lines.append(f"- 旧版: {diff.old_total_lines} 行 / {diff.old_section_count} 章节; "
                 f"新版: {diff.new_total_lines} 行 / {diff.new_section_count} 章节; "
                 f"成功配对: {diff.matched_pairs} 对")
    lines.append("")
    lines.append("## 摘要")
    lines.append("")
    lines.append("| 类别 | 数量 |")
    lines.append("| --- | --- |")
    for cat, count in sorted(diff.stats().items(),
                             key=lambda kv: -kv[1]):
        lines.append(f"| {CATEGORY_LABELS.get(cat, cat)} (`{cat}`) | {count} |")
    lines.append("")
    lines.append("## 明细（摘要中每条记录可展开为下表一行，二者可互相还原）")
    lines.append("")
    lines.append("| 类别 | 旧路径 | 新路径 | 内容Jaccard | 标题相似度 | 综合分 | 判定依据 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for c in diff.changes:
        if c.category == "unchanged":
            continue
        fmt = lambda v: "" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v))
        lines.append("| {} | {} | {} | {} | {} | {} | {} |".format(
            CATEGORY_LABELS.get(c.category, c.category),
            c.old_path or "—", c.new_path or "—",
            fmt(c.content_jaccard), fmt(c.title_sim), fmt(c.score),
            c.reason.replace("|", "\\|")))
    lines.append("")
    return "\n".join(lines)


def to_json(diff: DiffResult) -> str:
    return json.dumps(diff.to_dict(), ensure_ascii=False, indent=2)
