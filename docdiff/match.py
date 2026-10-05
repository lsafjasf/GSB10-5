"""跨版本章节匹配。

相似度模型（规则说明见 README）:
- 内容相似度 content_jaccard: 章节“自身正文”(不含子章节) shingle 集合的
  Jaccard 系数。shingle = 连续 SHINGLE_SIZE 个词元；CJK 文本按字符二元组
  切分（见 model 侧 tokenize），词元不足时退化为整段哈希。
  仅用自身正文做指纹的两个理由:
    1. 整章搬家时，章节身份由它自己的正文（含引言）与已配对的子节点共同
       决定，子节点不应再向父节点的指纹投票，否则父子会出现错配；
    2. 子树并集会产生 O(节点数*深度) 个集合成员，10 万行文档上内存爆炸。
- 标题相似度 title_sim: difflib.SequenceMatcher 比率（标题经大小写/空白归一化）。
- 综合分 score = CONTENT_WEIGHT*content_jaccard + TITLE_WEIGHT*title_sim。
- 判定: score >= threshold 才允许配对；相同 `{#anchor}` 强制配对；
  自身正文为空/极短的容器章节，由“已配对子节点的多数派父章节”推断配对。
- 指派: 候选对按 score 降序贪心指派，一对一；同分时标题相同者优先。

性能: 新版 shingle 倒排索引 + 旧版投票求交集，紧凑 int 编码，
跳过文档频率 > MAX_DF 的高频 shingle。
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Dict, FrozenSet, List, Optional, Tuple

from .model import Section, SectionIndex

SHINGLE_SIZE = 5
MIN_SHINGLES = 8            # 自身指纹少于该值的容器走“子节点推断”
MAX_DF = 40                 # shingle 在新版中的章节频率上限
CONTENT_WEIGHT = 0.8
TITLE_WEIGHT = 0.2
DEFAULT_THRESHOLD = 0.6
KEY_SHIFT = 16              # 配对紧凑键: (old_id << 16) | new_id（支持 65535 节/版）

# CJK 统一表意文字区间（基本区+扩展A+兼容表意）
_CJK = "㐀-䶿一-鿿豈-﫿"
CJK_RE = re.compile(f"[{_CJK}]")
# 单次扫描: CJK 连续串 或 非CJK词（\\w 中扣除 CJK 区间）
RUN_RE = re.compile(f"[{_CJK}]+|[^\\W{_CJK}]+")


def _norm_title(title: str) -> str:
    return re.sub(r"\s+", " ", title.strip().lower())


def _tokenize(text: str) -> List[str]:
    """词元化: 拉丁/数字按词; CJK 连续串按字符二元组(bigram)切分。

    中文没有空格分词，整行会被 \\w+ 视为单个词元，shingle 会过粗（一行改动
    就让 Jaccard 大幅下降）；CJK bigram 粒度与英文词相当，是常用做法。
    """
    tokens: List[str] = []
    append = tokens.append
    extend = tokens.extend
    for m in RUN_RE.finditer(text.lower()):
        run = m.group(0)
        if CJK_RE.match(run):
            n = len(run)
            if n == 1:
                append(run)
            else:
                extend(run[i:i + 2] for i in range(n - 1))
        else:
            append(run)
    return tokens


def _shingles(tokens: List[str]) -> FrozenSet[int]:
    if not tokens:
        return frozenset()
    if len(tokens) < SHINGLE_SIZE:
        return frozenset([zlib.crc32(" ".join(tokens).encode("utf-8"))])
    # 元组哈希只用于单次运行内的相等判断（不持久化），比 join+crc32 快得多
    return frozenset(
        hash(tuple(tokens[i:i + SHINGLE_SIZE]))
        for i in range(len(tokens) - SHINGLE_SIZE + 1)
    )


def build_own_shingles(index: SectionIndex) -> None:
    """就地填充每个章节的 own_shingles（仅自身正文，不展开子树）。"""
    for sec in index.sections:
        sec.own_shingles = _shingles(_tokenize("\n".join(sec.body_lines)))


@dataclass
class Candidate:
    old_id: int
    new_id: int
    content_jaccard: float
    title_sim: float
    score: float
    inferred: bool = False       # True=由已配对子节点推断的容器配对


@dataclass
class MatchResult:
    threshold: float
    mapping: Dict[int, int] = field(default_factory=dict)      # old_id -> new_id
    candidates: List[Candidate] = field(default_factory=list)  # 全部候选（诊断用）
    inferred_ids: set = field(default_factory=set)             # 推断配对的 old_id

    def new_to_old(self) -> Dict[int, int]:
        return {n: o for o, n in self.mapping.items()}


class Matcher:
    def __init__(self, threshold: float = DEFAULT_THRESHOLD,
                 content_weight: float = CONTENT_WEIGHT,
                 title_weight: float = TITLE_WEIGHT):
        self.threshold = threshold
        self.cw = content_weight
        self.tw = title_weight

    def match(self, old: SectionIndex, new: SectionIndex) -> MatchResult:
        build_own_shingles(old)
        build_own_shingles(new)
        candidates = self._candidates(old, new)
        mapping = self._assign(old, new, candidates)
        candidates, mapping, inferred = self._infer_containers(
            old, new, candidates, mapping)
        return MatchResult(threshold=self.threshold, mapping=mapping,
                           candidates=candidates, inferred_ids=inferred)

    # ---- 候选生成: 新版倒排 + 旧版投票 ----

    def _candidates(self, old: SectionIndex, new: SectionIndex) -> List[Candidate]:
        index: Dict[int, list] = {}
        capped: set = set()
        for sec in new.sections:
            if sec.id == 0:
                continue
            for sh in sec.own_shingles:
                lst = index.get(sh)
                if lst is None:
                    index[sh] = [sec.id]
                elif lst is not capped_sentinel:
                    if len(lst) >= MAX_DF:
                        index[sh] = capped_sentinel
                        capped.add(sh)
                    else:
                        lst.append(sec.id)

        pair_counts: Dict[int, int] = {}
        for sec in old.sections:
            if sec.id == 0 or not sec.own_shingles:
                continue
            seen: Dict[int, int] = {}
            for sh in sec.own_shingles:
                if sh in capped:
                    continue
                lst = index.get(sh)
                if lst:
                    for nid in lst:
                        seen[nid] = seen.get(nid, 0) + 1
            for nid, shared in seen.items():
                pair_counts[(sec.id << KEY_SHIFT) | nid] = shared

        title_cache: Dict[Tuple[str, str], float] = {}
        out: List[Candidate] = []
        for key, shared in pair_counts.items():
            o = key >> KEY_SHIFT
            n = key & ((1 << KEY_SHIFT) - 1)
            so, sn = old.sections[o], new.sections[n]
            union = len(so.own_shingles) + len(sn.own_shingles) - shared
            jaccard = shared / union if union else 0.0
            tkey = (_norm_title(so.title), _norm_title(sn.title))
            tsim = title_cache.get(tkey)
            if tsim is None:
                tsim = (1.0 if tkey[0] == tkey[1] else
                        SequenceMatcher(None, tkey[0], tkey[1]).ratio())
                title_cache[tkey] = tsim
            score = self.cw * jaccard + self.tw * tsim
            if score >= self.threshold:
                out.append(Candidate(o, n, jaccard, tsim, score))
        return out

    # ---- 贪心指派 ----

    def _assign(self, old: SectionIndex, new: SectionIndex,
                candidates: List[Candidate]) -> Dict[int, int]:
        mapping: Dict[int, int] = {0: 0}  # root 恒等
        used_old = {0}
        used_new = {0}

        new_by_anchor: Dict[str, int] = {}
        for sec in new.sections:
            if sec.anchor:
                new_by_anchor.setdefault(sec.anchor, sec.id)
        for sec in old.sections:
            if sec.anchor and sec.id not in used_old:
                nid = new_by_anchor.get(sec.anchor)
                if nid is not None and nid not in used_new:
                    mapping[sec.id] = nid
                    used_old.add(sec.id)
                    used_new.add(nid)

        old_titles = {s.id: _norm_title(s.title) for s in old.sections}
        new_titles = {s.id: _norm_title(s.title) for s in new.sections}
        for cand in sorted(
                candidates,
                key=lambda c: (-c.score,
                               0 if old_titles[c.old_id] == new_titles[c.new_id] else 1,
                               c.old_id, c.new_id)):
            if cand.old_id in used_old or cand.new_id in used_new:
                continue
            mapping[cand.old_id] = cand.new_id
            used_old.add(cand.old_id)
            used_new.add(cand.new_id)
        return mapping

    # ---- 容器推断: 短正文/空正文章节由已配对子节点的多数派父章节决定 ----

    def _infer_containers(self, old: SectionIndex, new: SectionIndex,
                          candidates: List[Candidate],
                          mapping: Dict[int, int]):
        inferred: set = set()
        for _ in range(2):  # 自底向上传播两轮即可覆盖两层无引言容器
            used_new = set(mapping.values())
            for osec in old.sections:
                if osec.id == 0 or osec.id in mapping:
                    continue
                if osec.own_shingles and len(osec.own_shingles) >= MIN_SHINGLES:
                    continue
                votes: Dict[int, int] = {}
                for child in osec.children:
                    nid = mapping.get(child.id)
                    if nid is not None and new.sections[nid].parent is not None:
                        pid = new.sections[nid].parent.id
                        votes[pid] = votes.get(pid, 0) + 1
                if not votes:
                    continue
                target, tally = max(votes.items(), key=lambda kv: kv[1])
                if target in used_new:
                    continue
                if tally * 2 <= len(osec.children):  # 必须严格多数
                    continue
                nsec = new.sections[target]
                tsim = (1.0 if _norm_title(osec.title) == _norm_title(nsec.title)
                        else SequenceMatcher(
                            None, _norm_title(osec.title),
                            _norm_title(nsec.title)).ratio())
                os_ = osec.own_shingles
                ns_ = nsec.own_shingles
                inter = len(os_ & ns_)
                union = len(os_) + len(ns_) - inter
                jaccard = inter / union if union else 0.0
                cand = Candidate(osec.id, target, jaccard, tsim,
                                 self.cw * jaccard + self.tw * tsim, inferred=True)
                candidates.append(cand)
                mapping[osec.id] = target
                used_new.add(target)
                inferred.add(osec.id)
        return candidates, mapping, inferred


# 超高频 shingle 的哨兵（倒排表条目被替换成它，投票时跳过）
capped_sentinel: list = []
