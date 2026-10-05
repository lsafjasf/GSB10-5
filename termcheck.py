"""termcheck —— 译名一致性检查库（仅依赖 Python 标准库）。

核心能力：
  * 载入术语库（规范译名 + 已知变体译名 + 可选上下文关键词）；
  * 在目标文档中定位变体译名、大小写/全半角不规范的译名；
  * 一词多义时依据上下文关键词区分：命中上下文 -> 冲突；未命中 -> 人工确认项；
  * 输出可复核的冲突清单（条目、位置、上下文片段）。

匹配规则详见 docs/MATCHING_RULES.md。
"""

from __future__ import annotations

import bisect
import unicodedata
from collections import deque
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 文本规范化
# ---------------------------------------------------------------------------


def _normalize_char(ch: str, _cache: dict = {}) -> str:
    """逐字符规范化：NFKC（全半角、兼容字符折叠）+ casefold（大小写折叠）。

    逐字符处理是为了能建立“规范化字符 -> 原文偏移”的精确映射；
    术语与文档使用同一函数，保证两侧规则一致。
    """
    r = _cache.get(ch)
    if r is None:
        r = unicodedata.normalize("NFKC", ch).casefold()
        _cache[ch] = r
    return r


def normalize_term(term: str) -> str:
    """规范化术语（与文档扫描使用的规则完全一致）。"""
    return "".join(_normalize_char(c) for c in term)


def normalize_text(text: str):
    """规范化整篇文档。

    返回 (normalized, offsets)：offsets[i] 是规范化后第 i 个字符在原文中的偏移，
    用于把命中位置映射回原文，保证冲突清单可复核。
    """
    out_parts = []
    offsets = []
    for i, ch in enumerate(text):
        n = _normalize_char(ch)
        if n:
            out_parts.append(n)
            offsets.extend((i,) * len(n))
    return "".join(out_parts), offsets


def _is_word_char(ch: str) -> bool:
    return ch.isascii() and (ch.isalnum() or ch == "_")


# ---------------------------------------------------------------------------
# 英语词形变化（仅对拉丁术语生成常见变形，不做词干还原，保证确定性）
# ---------------------------------------------------------------------------


def _inflect_word(w: str) -> set:
    forms = {w}
    if len(w) < 2:
        return forms
    # 复数
    if w.endswith("y") and w[-2] not in "aeiou":
        forms.add(w[:-1] + "ies")
    elif w.endswith(("s", "x", "z")) or w.endswith(("ch", "sh")):
        forms.add(w + "es")
    else:
        forms.add(w + "s")
    # -ing / -ed（含去 e、双写尾字母的常见情形）
    if w.endswith("e") and not w.endswith(("ee", "ye")):
        forms.add(w[:-1] + "ing")
        forms.add(w + "d")
    else:
        forms.add(w + "ing")
        forms.add(w + "ed")
    if len(w) >= 3 and w[-1] not in "aeiouwxy" and w[-2] in "aeiou" and w[-3] not in "aeiou":
        forms.add(w + w[-1] + "ing")  # run -> running
        forms.add(w + w[-1] + "ed")   # run -> runned(近似，双写规则)
    return forms


def inflect_term(term: str) -> set:
    """对纯拉丁术语生成词形变化（多词术语只变化最后一个词）。"""
    if not term.replace(" ", "").isascii() or not term.replace(" ", "").isalpha():
        return {term}
    words = term.split(" ")
    forms = _inflect_word(words[-1])
    return {" ".join(words[:-1] + [f]) if len(words) > 1 else f for f in forms}


# ---------------------------------------------------------------------------
# Aho-Corasick 多模式匹配自动机（数组存储，纯标准库）
# ---------------------------------------------------------------------------


class Automaton:
    __slots__ = ("children", "fail", "out", "patterns")

    def __init__(self):
        self.children = [None]   # node -> {char: node}，叶子为 None 以省内存
        self.fail = [0]
        self.out = [None]        # node -> [pattern_id, ...]（已合并失败链输出）
        self.patterns = []       # pattern_id -> 规范化后的模式串

    def add(self, pattern: str) -> int:
        pid = len(self.patterns)
        self.patterns.append(pattern)
        node = 0
        for ch in pattern:
            chd = self.children[node]
            if chd is None:
                chd = self.children[node] = {}
            nxt = chd.get(ch)
            if nxt is None:
                nxt = len(self.children)
                chd[ch] = nxt
                self.children.append(None)
                self.fail.append(0)
                self.out.append(None)
            node = nxt
        o = self.out[node]
        if o is None:
            self.out[node] = [pid]
        else:
            o.append(pid)
        return pid

    def build(self):
        children, fail, out = self.children, self.fail, self.out
        queue = deque()
        for nxt in children[0].values():
            queue.append(nxt)
        while queue:
            r = queue.popleft()
            chd = children[r]
            if not chd:
                continue
            for ch, s in chd.items():
                queue.append(s)
                f = fail[r]
                while f:
                    cf = children[f]
                    if cf is not None and ch in cf:
                        break
                    f = fail[f]
                cf = children[f]
                tgt = cf.get(ch, 0) if cf else 0
                fail[s] = tgt
                of = out[tgt]
                if of:
                    os_ = out[s]
                    out[s] = os_ + of if os_ else list(of)

    def scan(self, text: str):
        """产出 (end_index, pattern_id)，end_index 为命中串末尾+1。"""
        children, fail, out = self.children, self.fail, self.out
        node = 0
        for i, ch in enumerate(text):
            while node:
                chd = children[node]
                nxt = chd.get(ch) if chd else None
                if nxt is not None:
                    node = nxt
                    break
                node = fail[node]
            else:
                node = children[0].get(ch, 0)
            o = out[node]
            if o:
                for pid in o:
                    yield i + 1, pid


# ---------------------------------------------------------------------------
# 术语库
# ---------------------------------------------------------------------------

# 命中模式的角色
ROLE_APPROVED = "approved"   # 规范译名（含其词形变化）
ROLE_VARIANT = "variant"     # 已知变体译名（冲突对象）


@dataclass
class Entry:
    entry_id: str
    approved: str
    variants: list = field(default_factory=list)
    context_keywords: list = field(default_factory=list)  # 一词多义的消歧关键词
    note: str = ""


class Termbase:
    """术语库：TSV 格式，列 = id, 规范译名, 变体(;分隔), 上下文关键词(;分隔), 备注。"""

    def __init__(self, entries=None):
        self.entries = list(entries or [])

    @classmethod
    def load(cls, path: str) -> "Termbase":
        entries = []
        with open(path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.rstrip("\n")
                if not line or line.startswith("#"):
                    continue
                cols = line.split("\t")
                if len(cols) < 2:
                    raise ValueError(f"{path}:{lineno}: 至少需要 id 与规范译名两列")
                cols += [""] * (5 - len(cols))
                eid, approved, variants, ctx, note = cols[:5]
                entries.append(Entry(
                    entry_id=eid.strip(),
                    approved=approved.strip(),
                    variants=[v.strip() for v in variants.split(";") if v.strip()],
                    context_keywords=[k.strip() for k in ctx.split(";") if k.strip()],
                    note=note.strip(),
                ))
        return cls(entries)


# ---------------------------------------------------------------------------
# 检查结果
# ---------------------------------------------------------------------------

SEV_ERROR = "error"    # 确定的冲突
SEV_REVIEW = "review"  # 一词多义，需人工确认


@dataclass
class Finding:
    entry_id: str
    approved: str
    found: str       # 原文中实际出现的片段
    severity: str
    offset: int      # 原文字符偏移（0 起）
    line: int        # 1 起
    col: int         # 1 起
    reason: str
    context: str     # 原文上下文片段

    def to_dict(self):
        return dict(self.__dict__)


# ---------------------------------------------------------------------------
# 检查器
# ---------------------------------------------------------------------------

class Checker:
    def __init__(self, termbase: Termbase, context_window: int = 60):
        self.termbase = termbase
        self.context_window = context_window
        self.automaton = Automaton()
        # pattern_id -> (entry_index, role, 源串(未规范化的术语原形))
        self.pat_meta = []
        self._norm_approved = {}   # 规范化规范译名 -> [entry_index]，用于“变体恰是他人规范译名”的歧义检测
        self._build()

    # -- 构建 ------------------------------------------------------------

    def _add_pattern(self, term: str, entry_idx: int, role: str):
        norm = normalize_term(term)
        if not norm:
            return
        pid = self.automaton.add(norm)
        self.pat_meta.append((entry_idx, role, term))

    def _build(self):
        for idx, e in enumerate(self.termbase.entries):
            norm_approved = normalize_term(e.approved)
            self._norm_approved.setdefault(norm_approved, []).append(idx)
            # 规范译名 + 词形变化
            for form in inflect_term(e.approved):
                self._add_pattern(form, idx, ROLE_APPROVED)
            # 变体 + 词形变化；与规范译名规范化后相同的变体无意义，跳过
            for v in e.variants:
                if normalize_term(v) == norm_approved:
                    continue
                for form in inflect_term(v):
                    self._add_pattern(form, idx, ROLE_VARIANT)
        self.automaton.build()

    # -- 边界规则 ----------------------------------------------------------

    @staticmethod
    def _boundary_ok(text: str, start: int, end: int, pattern: str) -> bool:
        """拉丁术语要求两侧为词边界；CJK 术语不要求（中文无空格分词）。"""
        if pattern and _is_word_char(pattern[0]) and start > 0 and _is_word_char(text[start - 1]):
            return False
        if pattern and _is_word_char(pattern[-1]) and end < len(text) and _is_word_char(text[end]):
            return False
        return True

    # -- 主流程 ------------------------------------------------------------

    def check(self, text: str, source: str = "<text>") -> list:
        """检查文档，返回 Finding 列表（按原文位置排序）。"""
        norm_text, offsets = normalize_text(text)
        patterns = self.automaton.patterns

        # 1) 收集全部命中并做词边界过滤
        hits = []  # (start, end, pid)
        for end, pid in self.automaton.scan(norm_text):
            pat = patterns[pid]
            start = end - len(pat)
            if self._boundary_ok(norm_text, start, end, pat):
                hits.append((start, end, pid))

        # 2) 最长匹配优先：被更长命中完全包含的命中丢弃
        #    （例：规范译名“苹果公司”命中时，其中的变体“苹果”不再单报）
        # 按起点升序、同起点长者在前排序。
        # 命中 h 被“严格更长”的已保留命中包含时丢弃；
        # 跨度完全相同的命中（同一原文片段命中多个角色，如甲的变体=乙的规范译名）全部保留，
        # 交给后续歧义判定。
        hits.sort(key=lambda h: (h[0], -(h[1] - h[0])))
        kept = []
        max_end = -1    # 覆盖屏障：已保留命中的最大终点
        max_start = -1  # 达到该终点的命中的最小起点
        for h in hits:
            if h[1] < max_end or (h[1] == max_end and max_start < h[0]):
                continue
            kept.append(h)
            if h[1] > max_end:
                max_end, max_start = h[1], h[0]

        # 3) 分类与报告
        line_starts = [i for i, c in enumerate(text) if c == "\n"]
        # 句边界（规范化文本）：一词多义的上下文关键词只在“同一句且距离窗口内”生效，
        # 避免相邻句子的关键词跨句污染消歧结果。
        sent_sep = set("。！？!?;\n；")
        bounds = [i for i, c in enumerate(norm_text) if c in sent_sep]
        findings = []
        for start, end, pid in kept:
            entry_idx, role, source_form = self.pat_meta[pid]
            entry = self.termbase.entries[entry_idx]
            o_start = offsets[start]
            o_end = offsets[end - 1] + 1
            found = text[o_start:o_end]
            line = bisect.bisect_right(line_starts, o_start)
            col = o_start - (line_starts[line - 1] + 1 if line else 0) + 1
            line += 1
            ctx_lo = max(0, o_start - 30)
            ctx_hi = min(len(text), o_end + 30)
            snippet = text[ctx_lo:ctx_hi].replace("\n", " ")

            if role == ROLE_APPROVED:
                if found == entry.approved or found in inflect_term(entry.approved):
                    continue  # 规范用法（含词形变化），不报告
                findings.append(Finding(
                    entry.entry_id, entry.approved, found, SEV_ERROR,
                    o_start, line, col,
                    "大小写/全半角与规范译名不一致", snippet))
                continue

            # 变体命中：一词多义消歧
            if entry.context_keywords:
                bi = bisect.bisect_right(bounds, start)
                s0 = bounds[bi - 1] + 1 if bi else 0
                s1 = bounds[bi] if bi < len(bounds) else len(norm_text)
                win_lo = max(s0, start - self.context_window)
                win_hi = min(s1, end + self.context_window)
                window = norm_text[win_lo:win_hi]
                if not any(normalize_term(k) in window for k in entry.context_keywords):
                    findings.append(Finding(
                        entry.entry_id, entry.approved, found, SEV_REVIEW,
                        o_start, line, col,
                        "一词多义：未命中上下文关键词，需人工确认", snippet))
                    continue

            # 变体同时是其他条目的规范译名 -> 歧义，人工确认
            norm_found = normalize_term(found)
            others = [i for i in self._norm_approved.get(norm_found, []) if i != entry_idx]
            if others:
                findings.append(Finding(
                    entry.entry_id, entry.approved, found, SEV_REVIEW,
                    o_start, line, col,
                    "歧义：该译名同时是条目 "
                    + ",".join(self.termbase.entries[i].entry_id for i in others)
                    + " 的规范译名，需人工确认", snippet))
                continue

            findings.append(Finding(
                entry.entry_id, entry.approved, found, SEV_ERROR,
                o_start, line, col,
                "使用了非规范译名（变体）", snippet))

        findings.sort(key=lambda f: f.offset)
        return findings


def summarize(findings):
    errors = [f for f in findings if f.severity == SEV_ERROR]
    reviews = [f for f in findings if f.severity == SEV_REVIEW]
    return errors, reviews
