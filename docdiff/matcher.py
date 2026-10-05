"""跨版本章节配对（1:1 匹配，禁止一对多）。

两阶段：
1. 精确配对：(正文指纹, 规范化标题, 层级) 完全相同，直接配对，分数 1.0；
2. 候选配对：正文 2-shingle 倒排 + 同标题召回候选，计算
   score = 0.7 * body_jaccard + 0.3 * title_sim，body_jaccard >= tau_body
   才接受；无正文节只能靠标题信号 title_sim >= tau_title（且层级相同）。
所有候选按分数降序贪心接受，已配对的旧/新节不再参与。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Tuple

from .fingerprint import jaccard, title_sim
from .model import Tree

MAX_POSTING = 200     # 单个 shingle 命中节数超过该值视为样板文本，忽略
MAX_CANDIDATES = 30   # 每节参与打分的最大候选数


def match(old: Tree, new: Tree, tau_body: float = 0.60,
          tau_title: float = 0.70) -> Tuple[Dict[int, int], Dict[int, float]]:
    matched: Dict[int, int] = {}
    used_new = set()
    scores: Dict[int, float] = {}

    # ---- 阶段 1：精确配对 ----
    key_new: Dict[tuple, List[int]] = defaultdict(list)
    for sec in new.sections.values():
        key_new[(sec.body_fp, sec.ntitle, sec.level)].append(sec.sid)
    for sec in old.sections.values():
        bucket = key_new.get((sec.body_fp, sec.ntitle, sec.level))
        if bucket:
            nsid = next((s for s in bucket if s not in used_new), None)
            if nsid is not None:
                matched[sec.sid] = nsid
                used_new.add(nsid)
                scores[sec.sid] = 1.0

    # ---- 阶段 2：候选生成 ----
    posting: Dict[int, List[int]] = defaultdict(list)
    title_posting: Dict[str, List[int]] = defaultdict(list)
    empty_new: List[int] = []
    for sec in new.sections.values():
        if sec.sid in used_new:
            continue
        if sec.body_lines:
            for sh in sec.body_shingles:
                posting[sh].append(sec.sid)
            title_posting[sec.ntitle].append(sec.sid)
        else:
            empty_new.append(sec.sid)

    edges: List[Tuple[float, int, int]] = []
    for sec in old.sections.values():
        if sec.sid in matched:
            continue
        if not sec.body_lines:
            # 无正文节：候选为所有未配对的无正文新节，仅靠标题相似度
            for nsid in empty_new:
                nsec = new.sections[nsid]
                if nsec.level != sec.level:
                    continue
                ts = title_sim(sec.ntitle, nsec.ntitle)
                if ts >= tau_title:
                    edges.append((ts, sec.sid, nsid))
            continue
        counts: Dict[int, int] = defaultdict(int)
        for sh in sec.body_shingles:
            plist = posting.get(sh)
            if plist and len(plist) <= MAX_POSTING:
                for nsid in plist:
                    counts[nsid] += 1
        for nsid in title_posting.get(sec.ntitle, []):
            counts[nsid] += 1000000
        if not counts:
            continue
        top = sorted(counts.items(), key=lambda kv: -kv[1])[:MAX_CANDIDATES]
        for nsid, _ in top:
            nsec = new.sections[nsid]
            if not nsec.body_lines:
                continue
            bs = jaccard(sec.body_shingles, nsec.body_shingles)
            if bs < tau_body:
                continue
            ts = title_sim(sec.ntitle, nsec.ntitle)
            edges.append((0.7 * bs + 0.3 * ts, sec.sid, nsid))

    # ---- 阶段 3：贪心接受 ----
    edges.sort(key=lambda e: (-e[0], e[1], e[2]))
    for score, osid, nsid in edges:
        if osid in matched or nsid in used_new:
            continue
        matched[osid] = nsid
        used_new.add(nsid)
        scores[osid] = score

    return matched, scores
