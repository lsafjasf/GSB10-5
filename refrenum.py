#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""refrenum: 按正文首次出现顺序重排文献引用编号（仅标准库）。

编号规则（摘要，详见 docs/RULES.md）：
  1. 正文中的引用标记形如 [n]、[n,m]、[n-m]（n、m 为十进制正整数，
     逗号/连字符前后允许空格），方括号内只允许数字、逗号、连字符、空格。
  2. 正文按从左到右扫描；同一方括号内按从左到右展开（区间 n-m 展开为
     n, n+1, ..., m）。每个被引用的“条目编号”按其在正文中的首次出现
     顺序分配新编号 1, 2, 3, ...；同一编号再次出现（重复引用）沿用首次
     分配的新编号，不占用新的编号。
  3. 文末“参考文献”列表按新编号升序重写，只保留被引用过的条目；
     从未被引用的条目（孤立条目）不再出现，并在报告中列出。
  4. 引用了不存在的条目（悬空引用）：该处标记保持原样不改写，并报告。
  5. 旧编号中的跳号（列表存在但未被任何引用占用的整数）分类报告。

回写规则：
  - 正文除引用标记方括号内部外逐字节不变；标记内数字替换为新编号，
    数字之间的分隔符（逗号/连字符及其两侧空格）原样保留。
  - 区间标记（含连字符）展开后若新编号不再连续，则连字符改写为逗号，
    以保证新标记语义正确。
"""

import json
import re
import sys

# 一个数字组：单个数字，或 n-m 区间（允许两侧空格）
_GROUP_RE = re.compile(rb"(\d+)(?:[ \t]*-[ \t]*(\d+))?")
# 完整引用标记：[ 组 (分隔符 组)* ]，分隔符为逗号（允许两侧空格）
_MARKER_RE = re.compile(
    rb"\[(\d+(?:[ \t]*-[ \t]*\d+)?(?:[ \t]*,[ \t]*\d+(?:[ \t]*-[ \t]*\d+)?)*)\]"
)
# 文末条目行：[n] 开头（允许前导空白），其后为条目文本
_ENTRY_RE = re.compile(rb"^[ \t]*\[(\d+)\][ \t]?(.*)$")
# 参考文献节标题（整行匹配，忽略大小写与首尾空白）
_HEADING_RE = re.compile(
    rb"^[ \t]*#*[ \t]*(?:\xe5\x8f\x82\xe8\x80\x83\xe6\x96\x87\xe7\x8c\xae|references)[ \t]*$",
    re.IGNORECASE,
)

__all__ = [
    "RefrenumError",
    "parse_document",
    "renumber",
    "render",
    "verify",
    "byte_compare",
    "build_report",
    "process",
]


class RefrenumError(Exception):
    """输入文档不合法（如缺参考文献节、条目编号重复）。"""


def _parse_marker_inner(inner):
    """解析方括号内部，返回 (groups, seps, ids)。

    groups: [(lo, hi)]，单数字组 hi 为 None，区间组为 (lo, hi)；
    seps:   组间分隔符原文（bytes，含逗号与空格）；
    ids:    展开后的条目编号列表（区间已展开）。
    """
    groups = []
    seps = []
    ids = []
    pos = 0
    first = True
    while pos < len(inner):
        if not first:
            m = re.match(rb"[ \t]*,[ \t]*", inner[pos:])
            seps.append(m.group(0))
            pos += m.end()
        m = _GROUP_RE.match(inner, pos)
        lo = int(m.group(1))
        hi = int(m.group(2)) if m.group(2) is not None else None
        groups.append((lo, hi))
        if hi is not None:
            if hi < lo:
                raise RefrenumError("区间左端大于右端: [%d-%d]" % (lo, hi))
            ids.extend(range(lo, hi + 1))
        else:
            ids.append(lo)
        pos = m.end()
        first = False
    return groups, seps, ids


def parse_document(data):
    """把文档 bytes 拆成 (body_bytes, entries)。

    entries: {旧编号: 条目文本 bytes}（不含方括号与编号）。
    参考文献节中除标题行与条目行外的内容（空行、注释等）将被丢弃。
    """
    lines = data.split(b"\n")
    head_idx = None
    for i, line in enumerate(lines):
        if _HEADING_RE.match(line):
            head_idx = i
            break
    if head_idx is None:
        raise RefrenumError("未找到参考文献节标题（'参考文献' 或 'References'）")
    body = b"\n".join(lines[:head_idx])
    entries = {}
    for line in lines[head_idx + 1:]:
        m = _ENTRY_RE.match(line)
        if not m:
            continue
        num = int(m.group(1))
        if num in entries:
            raise RefrenumError("参考文献条目编号重复: [%d]" % num)
        entries[num] = m.group(2)
    return body, entries


def _scan_body(body):
    """扫描正文，返回标记列表 [(start, end, groups, seps, ids)]。"""
    markers = []
    for m in _MARKER_RE.finditer(body):
        groups, seps, ids = _parse_marker_inner(m.group(1))
        markers.append((m.start(), m.end(), groups, seps, ids))
    return markers


def renumber(body, entries):
    """核心重排。返回 (new_body, old2new, dangling_ids, replacements)。

    replacements: [(start, end, new_marker_bytes)]，用于逐字节一致性校验。
    """
    markers = _scan_body(body)
    old2new = {}
    dangling = set()
    replacements = []
    out = []
    prev = 0
    for start, end, groups, seps, ids in markers:
        if any(i not in entries for i in ids):
            dangling.update(i for i in ids if i not in entries)
            continue  # 悬空引用：保持原样
        new_nums = []
        for i in ids:
            if i not in old2new:
                old2new[i] = len(old2new) + 1
            new_nums.append(old2new[i])
        # 渲染新标记：逐组替换数字；区间组展开后若不连续则降级为逗号列表
        parts = []
        cursor = 0
        for gi, (lo, hi) in enumerate(groups):
            if gi > 0:
                parts.append(seps[gi - 1])
            if hi is not None:
                seg_len = hi - lo + 1
                seg = new_nums[cursor:cursor + seg_len]
                cursor += seg_len
                if seg == list(range(seg[0], seg[0] + len(seg))):
                    parts.append(str(seg[0]).encode())
                    if len(seg) > 1:
                        parts.append(b"-" + str(seg[-1]).encode())
                else:
                    parts.append(b",".join(str(x).encode() for x in seg))
            else:
                parts.append(str(new_nums[cursor]).encode())
                cursor += 1
        new_marker = b"[" + b"".join(parts) + b"]"
        replacements.append((start, end, new_marker))
        out.append(body[prev:start])
        out.append(new_marker)
        prev = end
    out.append(body[prev:])
    return b"".join(out), old2new, sorted(dangling), replacements


def render(new_body, entries, old2new):
    """拼接新文档：新正文 + 参考文献节（按新编号升序，仅被引用条目）。"""
    lines = [new_body, b"## \xe5\x8f\x82\xe8\x80\x83\xe6\x96\x87\xe7\x8c\xae", b""]
    for old, new in sorted(old2new.items(), key=lambda kv: kv[1]):
        lines.append(b"[%d] " % new + entries[old])
    return b"\n".join(lines)


def verify(new_body, new_entries_count, replacements, orig_body, dangling=()):
    """回写后一致性校验，返回 (ok, checks)。

    checks: [(名称, 是否通过, 说明)]
    """
    checks = []
    dangling = set(dangling)
    all_markers = _scan_body(new_body)
    markers = [mk for mk in all_markers
               if not any(i in dangling for i in mk[4])]

    # 1. 新正文引用编号为 1..N 连续无跳号，且按首次出现单调分配
    seen = {}
    order_ok = True
    for _, _, _, _, ids in markers:
        for i in ids:
            if i not in seen:
                if i != len(seen) + 1:
                    order_ok = False
                seen[i] = len(seen) + 1
    contiguous = sorted(seen) == list(range(1, len(seen) + 1))
    checks.append(("new_ids_contiguous_1_to_N", contiguous,
                   "新编号集合为 1..%d 连续" % len(seen)))
    checks.append(("first_appearance_order", order_ok,
                   "新编号按正文首次出现顺序分配"))
    checks.append(("cited_count_matches_list", len(seen) == new_entries_count,
                   "正文被引条目数(%d) == 文末条目数(%d)" % (len(seen), new_entries_count)))

    # 2. 用记录的替换段重建新正文，验证与扫描结果一致（无遗漏/错位）
    rebuilt = []
    prev = 0
    for start, end, new_marker in replacements:
        rebuilt.append(orig_body[prev:start])
        rebuilt.append(new_marker)
        prev = end
    rebuilt.append(orig_body[prev:])
    checks.append(("rewrite_reconstruction", b"".join(rebuilt) == new_body,
                   "由替换记录重建的正文与实际输出逐字节一致"))

    # 3. 标记数不变、括号配平
    orig_marker_count = len(_scan_body(orig_body))
    checks.append(("marker_count_preserved",
                   len(all_markers) == orig_marker_count,
                   "重写前后标记数一致（%d，含原样保留的悬空引用）" % len(all_markers)))
    checks.append(("brackets_balanced",
                   new_body.count(b"[") == new_body.count(b"]"),
                   "方括号配平"))
    ok = all(c[1] for c in checks)
    return ok, checks


def byte_compare(orig_body, new_body, replacements):
    """重排前后正文逐字节对比。

    返回 (identical_outside_markers, diff_count, details)。
    将每个被改写的标记段视为一个“允许变更窗口”，窗口之外必须逐字节相同。
    """
    diffs = []
    op = np = 0
    ok = True
    for start, end, new_marker in replacements:
        gap_len = start - op
        if orig_body[op:start] != new_body[np:np + gap_len]:
            ok = False
            diffs.append({"offset": op, "type": "unexpected_change_outside_marker"})
        old_seg = orig_body[start:end]
        if old_seg != new_marker:
            diffs.append({
                "offset": np + gap_len,
                "type": "marker_renumbered",
                "old": old_seg.decode("utf-8", "replace"),
                "new": new_marker.decode("utf-8", "replace"),
            })
        op = end
        np += gap_len + len(new_marker)
    if orig_body[op:] != new_body[np:]:
        ok = False
        diffs.append({"offset": op, "type": "unexpected_change_outside_marker"})
    marker_changes = sum(1 for d in diffs if d["type"] == "marker_renumbered")
    return ok, marker_changes, diffs


def build_report(body, entries, old2new, dangling, verify_checks, cmp_info):
    """汇总分类报告（dict，可直接 json 序列化）。"""
    entry_ids = sorted(entries)
    cited_old = set(old2new)
    gaps = [n for n in range(entry_ids[0], entry_ids[-1] + 1)
            if n not in entries] if entry_ids else []
    orphan = [n for n in entry_ids if n not in cited_old]
    identical, marker_changes, diffs = cmp_info
    return {
        "mapping_old_to_new": {str(k): old2new[k]
                               for k in sorted(old2new, key=old2new.get)},
        "issues": {
            "dangling_citations": dangling,
            "orphan_entries": orphan,
            "numbering_gaps_in_old_list": gaps,
        },
        "counts": {
            "entries_total": len(entries),
            "entries_cited": len(cited_old),
            "citation_marks_rewritten": marker_changes,
        },
        "verification": {
            "ok": all(c[1] for c in verify_checks),
            "checks": [{"name": n, "pass": p, "detail": d}
                       for n, p, d in verify_checks],
        },
        "byte_compare": {
            "body_identical_outside_markers": identical,
            "marker_segments_changed": marker_changes,
            "diffs": diffs,
        },
    }


def process(data):
    """端到端：bytes -> (new_document_bytes, report_dict)。"""
    body, entries = parse_document(data)
    new_body, old2new, dangling, replacements = renumber(body, entries)
    ok_checks, checks = verify(new_body, len(old2new), replacements, body, dangling)
    cmp_info = byte_compare(body, new_body, replacements)
    report = build_report(body, entries, old2new, dangling, checks, cmp_info)
    report["verification"]["ok"] = report["verification"]["ok"] and ok_checks
    return render(new_body, entries, old2new), report


def main(argv):
    import argparse
    p = argparse.ArgumentParser(description="按正文首次出现顺序重排文献引用编号")
    p.add_argument("input", help="输入文档（UTF-8 文本）")
    p.add_argument("-o", "--output", help="重排后的输出文档")
    p.add_argument("-r", "--report", help="报告 JSON 输出路径（缺省打印到 stdout）")
    args = p.parse_args(argv)

    with open(args.input, "rb") as f:
        data = f.read()
    new_doc, report = process(data)
    if args.output:
        with open(args.output, "wb") as f:
            f.write(new_doc)
    js = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(js + "\n")
    else:
        sys.stdout.write(js + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
