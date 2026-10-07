"""译名一致性检查器。

输入：术语库 + 语料（JSONL，每行 {"id", "source", "target"}；target 可空，
source 可空表示纯中文文档检查）。

输出：Report，含
  - conflicts:        确定的冲突（forbidden_translation / wrong_sense /
                      multiple_renderings）
  - review:           需人工确认（ambiguous_sense / unlisted_translation /
                      forbidden_unverified）
  - termbase_issues:  术语库自身问题
每条记录都带 entry_id、segment id、原文 offset 与行列号，可复核。

匹配流程（规则细节见 README「匹配规则」）：
  1. 源文、译文分别做 NFKC + 空白折叠归一化，保留下标映射；
  2. 英文侧：token 化 -> Aho-Corasick 多模式匹配（术语表词形已展开），
     缩写条目（API 等）额外要求原文全大写；
  3. 中文侧：对全部译名做 Aho-Corasick 扫描，取「最长且不重叠」的命中；
  4. 一词多义：用义项 cue 词在源文上下文窗口内打分，唯一正分最高分
     胜出，否则进人工确认列表；
  5. 汇总：同一条目在全文档出现多种译名且未声明 allow_multiple 时，
     报 multiple_renderings 冲突。
"""

import json
import time

from .ahocorasick import AhoCorasick
from .lemma import tokens as en_tokens
from .normalize import normalize, line_col

__all__ = ["Checker", "Report", "check_files"]

DEFAULT_CUE_WINDOW = 80  # 义项 cue 词上下文窗口（归一化后的字符数）


class Report:
    def __init__(self):
        self.conflicts = []
        self.review = []
        self.termbase_issues = []
        self.stats = {}

    def to_dict(self):
        return {
            "conflicts": self.conflicts,
            "review": self.review,
            "termbase_issues": self.termbase_issues,
            "stats": self.stats,
        }

    def summary(self):
        by_type = {}
        for c in self.conflicts:
            by_type[c["type"]] = by_type.get(c["type"], 0) + 1
        review_by_type = {}
        for r in self.review:
            review_by_type[r["type"]] = review_by_type.get(r["type"], 0) + 1
        return {"conflicts": by_type, "review": review_by_type,
                "termbase_issues": len(self.termbase_issues)}


class Checker:
    def __init__(self, termbase, cue_window=DEFAULT_CUE_WINDOW):
        self.tb = termbase
        self.cue_window = cue_window
        self._build_english_matcher()
        self._build_chinese_matcher()

    # ------------------------------------------------------------------ #
    # 自动机构建
    # ------------------------------------------------------------------ #
    def _build_english_matcher(self):
        ac = AhoCorasick()
        # payload: (entry_id, acronym_only, pattern_len_in_tokens)
        self.en_payloads = []
        for form, owners in self.tb.head_forms.items():
            for entry_id, acronym_only in owners:
                pid = len(self.en_payloads)
                self.en_payloads.append((entry_id, acronym_only, 1))
                ac.add([form], pid)
        ac.freeze()
        self.en_ac = ac
        # 多词术语：按首词原形索引，扫描时在 token 流上逐词位比对词形集合
        self.mw_by_first = {}
        self.mw_patterns = []  # (entry_id, base_seq, formsets)
        # 表层形式 -> 可能的原形（仅多词术语需要，单词术语走 AC）
        self.form_to_lemmas = {}
        for entry_id, (base, formsets) in self.tb.multiword.items():
            idx = len(self.mw_patterns)
            self.mw_patterns.append((entry_id, base, formsets))
            self.mw_by_first.setdefault(base[0], []).append(idx)
            for lemma, forms in zip(base, formsets):
                for f in forms:
                    self.form_to_lemmas.setdefault(f, set()).add(lemma)

    def _build_chinese_matcher(self):
        ac = AhoCorasick()
        # 安全组合词（如「归档」之于禁用译名「档」）也加入自动机，
        # 最长不重叠筛选时它们会遮蔽落在其内部的禁用译名命中。
        self.safe_patterns = {}
        for entry_id, text in self.tb.safe_embeddings:
            pid = len(self.tb.render_meta) + len(self.safe_patterns)
            self.safe_patterns[pid] = (entry_id, text)
            ac.add(text, pid)
        for rid, meta in enumerate(self.tb.render_meta):
            ac.add(meta["text"], rid)
        ac.freeze()
        self.zh_ac = ac

    # ------------------------------------------------------------------ #
    # 英文匹配
    # ------------------------------------------------------------------ #
    def _match_english(self, norm_text, orig_text):
        """返回 [(entry_id, sense 无关, orig_offset, orig_end, surface)]"""
        toks = en_tokens(norm_text.lower())
        toks_orig = en_tokens(norm_text)  # 保大小写，用于缩写校验
        hits = []
        for end_i, pid in self.en_ac.search([t[0] for t in toks]):
            entry_id, acronym_only, plen = self.en_payloads[pid]
            start_i = end_i - plen + 1
            if start_i < 0:
                continue
            if acronym_only:
                # 缩写条目：原文对应 token 必须全大写（APIs 允许）
                ok = True
                for k in range(start_i, end_i + 1):
                    surface = toks_orig[k][0]
                    if surface.isupper() or (surface.endswith("s")
                                             and surface[:-1].isupper()):
                        pass
                    else:
                        ok = False
                        break
                if not ok:
                    continue
            start = toks[start_i][1]
            end = toks[end_i][2]
            hits.append((start, end, entry_id))
        # 多词术语
        n = len(toks)
        for i in range(n):
            lemmas = self.form_to_lemmas.get(toks[i][0])
            if not lemmas:
                continue
            for lemma in lemmas:
                for idx in self.mw_by_first.get(lemma, ()):
                    entry_id, base, formsets = self.mw_patterns[idx]
                    plen = len(base)
                    if i + plen > n:
                        continue
                    if all(toks[i + k][0] in formsets[k]
                           for k in range(plen)):
                        hits.append((toks[i][1], toks[i + plen - 1][2],
                                     entry_id))
        # 最长不重叠
        hits.sort(key=lambda h: (h[0], -(h[1] - h[0])))
        selected = []
        last_end = -1
        for start, end, entry_id in hits:
            if start >= last_end:
                selected.append((start, end, entry_id))
                last_end = end
        return selected

    # ------------------------------------------------------------------ #
    # 中文匹配
    # ------------------------------------------------------------------ #
    def _match_chinese(self, norm_text):
        hits = []
        for end_i, rid in self.zh_ac.search(norm_text):
            if rid in self.safe_patterns:
                text = self.safe_patterns[rid][1]
            else:
                text = self.tb.render_meta[rid]["text"]
            hits.append((end_i - len(text) + 1, end_i + 1, rid))
        hits.sort(key=lambda h: (h[0], -(h[1] - h[0])))
        selected = []
        last_end = -1
        for start, end, rid in hits:
            if start >= last_end:
                selected.append((start, end, rid))
                last_end = end
        return selected

    # ------------------------------------------------------------------ #
    # 义项消歧
    # ------------------------------------------------------------------ #
    def _resolve_sense(self, entry, norm_src, start, end):
        """返回 (sense_id 或 None, 得分说明)。None 表示无法确定。"""
        senses = entry["senses"]
        if len(senses) == 1:
            return senses[0].sense_id, "single"
        lo = max(0, start - self.cue_window)
        hi = min(len(norm_src), end + self.cue_window)
        window = norm_src[lo:hi].lower()
        best, best_score, tied = None, 0, False
        for s in senses:
            score = sum(1 for c in s.pos_cues if c in window)
            score -= sum(1 for c in s.neg_cues if c in window)
            if score > best_score:
                best, best_score, tied = s.sense_id, score, False
            elif score == best_score and score > 0:
                tied = True
        if best is not None and not tied and best_score > 0:
            return best, "cues score=%d" % best_score
        return None, "no decisive cue"

    # ------------------------------------------------------------------ #
    # 主流程
    # ------------------------------------------------------------------ #
    def check(self, segments):
        t0 = time.perf_counter()
        report = Report()
        report.termbase_issues = list(self.tb.issues)
        # (条目, 义项) -> {render_text: {...}} 全文档聚合；按义项聚合，
        # 一词多义的不同义项天然有不同译名，不算冲突
        render_usage = {}
        n_src_match = n_zh_match = 0
        n_segments = 0

        for seg in segments:
            n_segments += 1
            seg_id = seg.get("id", "?")
            src = seg.get("source") or ""
            tgt = seg.get("target") or ""
            norm_src, map_src = normalize(src)
            norm_tgt, map_tgt = normalize(tgt)

            en_hits = self._match_english(norm_src, src) if norm_src else []
            zh_hits = self._match_chinese(norm_tgt) if norm_tgt else []
            n_src_match += len(en_hits)
            n_zh_match += len(zh_hits)

            # 每个条目在本段的义项判定
            entry_sense = {}   # entry_id -> (sense_id 或 None, why, pos)
            wrong_sense_entries = set()  # 本段已报 wrong_sense 的条目
            for start, end, entry_id in en_hits:
                entry = self.tb.entries[entry_id]
                sid, why = self._resolve_sense(entry, norm_src, start, end)
                prev = entry_sense.get(entry_id)
                if prev is not None and prev[0] != sid:
                    sid, why = None, "conflicting senses in segment"
                entry_sense[entry_id] = (sid, why, start)
                if sid is None and len(entry["senses"]) > 1:
                    o = map_src[start]
                    line, col = line_col(src, o)
                    report.review.append({
                        "type": "ambiguous_sense",
                        "entry_id": entry_id,
                        "source": entry["source"],
                        "segment": seg_id, "offset": o,
                        "line": line, "col": col,
                        "context": src[max(0, o - 30):o + 30],
                        "message": "无法根据 cue 词确定义项，需人工确认",
                    })

            # 中文命中逐条判定
            for start, end, rid in zh_hits:
                if rid in self.safe_patterns:
                    continue  # 安全组合词，如「归档」之于禁用译名「档」
                meta = self.tb.render_meta[rid]
                if rid in self.tb.shared_renders:
                    continue  # 术语库归属冲突，见 termbase_issues
                owners = self.tb.render_owners(rid)
                kinds = {k for _, _, k in owners}
                o = map_tgt[start]
                line, col = line_col(tgt, o)
                pos = {"segment": seg_id, "offset": o,
                       "line": line, "col": col}

                if "forbidden" in kinds:
                    entry_id = next(e for e, _, k in owners if k == "forbidden")
                    if entry_id in entry_sense or not norm_src:
                        # 源文佐证了该条目，或纯中文文档（降级为待确认）
                        if entry_id in entry_sense:
                            report.conflicts.append({
                                "type": "forbidden_translation",
                                "entry_id": entry_id,
                                "source": self.tb.entries[entry_id]["source"],
                                "render": meta["text"], **pos,
                                "message": "使用了禁用的译名 %r" % meta["text"],
                            })
                        else:
                            report.review.append({
                                "type": "forbidden_unverified",
                                "entry_id": entry_id,
                                "render": meta["text"], **pos,
                                "message": "纯中文文档中出现禁用译名，需人工确认",
                            })
                    continue

                # canonical / synonym
                for entry_id, sense_id, kind in owners:
                    usage = render_usage.setdefault((entry_id, sense_id), {})
                    if meta["text"] not in usage:
                        usage[meta["text"]] = {"kind": kind, "first": pos}
                    if entry_id not in entry_sense:
                        continue  # 本段源文未出现该条目，归属存疑，仅计入聚合
                    expected_sid, why, _ = entry_sense[entry_id]
                    if expected_sid is not None and sense_id != expected_sid:
                        wrong_sense_entries.add(entry_id)
                        report.conflicts.append({
                            "type": "wrong_sense",
                            "entry_id": entry_id,
                            "source": self.tb.entries[entry_id]["source"],
                            "render": meta["text"],
                            "expected_sense": expected_sid,
                            "actual_sense": sense_id, **pos,
                            "message": "译名 %r 属于义项 %s，本段应为义项 %s"
                                       % (meta["text"], sense_id, expected_sid),
                        })

            # 源文出现条目但译文未见其任何登记译名 -> 疑似未按术语表翻译
            if norm_src and norm_tgt:
                for entry_id, (sid, why, _) in entry_sense.items():
                    entry = self.tb.entries[entry_id]
                    if sid is None:
                        continue  # 义项未定，已有 ambiguous_sense
                    sense = self.tb.sense(entry_id, sid)
                    if entry_id in wrong_sense_entries:
                        continue  # 已有更强的 wrong_sense 冲突
                    known = {sense.canonical} | set(sense.synonyms) \
                        | set(sense.forbidden)
                    if not any(r in norm_tgt for r in known):
                        report.review.append({
                            "type": "unlisted_translation",
                            "entry_id": entry_id,
                            "source": entry["source"],
                            "segment": seg_id,
                            "message": "译文中未出现该条目的任何登记译名，"
                                       "可能漏译或使用了未登记译名",
                        })

        # 多种译名并存（全文档聚合，按义项）
        for (entry_id, sense_id), usage in sorted(render_usage.items()):
            entry = self.tb.entries[entry_id]
            if entry["allow_multiple"]:
                continue
            approved = {t: m for t, m in usage.items()
                        if m["kind"] in ("canonical", "synonym")}
            if len(approved) > 1:
                occ = [{"render": t, "kind": m["kind"], **m["first"]}
                       for t, m in sorted(approved.items())]
                report.conflicts.append({
                    "type": "multiple_renderings",
                    "entry_id": entry_id,
                    "sense_id": sense_id,
                    "source": entry["source"],
                    "renderings": occ,
                    "message": "义项 %s 使用了 %d 种译名且未声明 "
                               "allow_multiple: %s"
                               % (sense_id, len(approved),
                                  "、".join(sorted(approved))),
                })

        report.stats = {
            "segments": n_segments,
            "entries": len(self.tb.entries),
            "renders": len(self.tb.render_meta),
            "source_matches": n_src_match,
            "target_matches": n_zh_match,
            "elapsed_sec": round(time.perf_counter() - t0, 3),
        }
        return report


def iter_segments(path):
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def check_files(termbase_path, corpus_path, cue_window=DEFAULT_CUE_WINDOW):
    from .termbase import load_file
    tb = load_file(termbase_path)
    checker = Checker(tb, cue_window=cue_window)
    return checker.check(list(iter_segments(corpus_path)))
