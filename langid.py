"""
langid.py - 纯 Python 标准库文本语言识别库

支持语言: en zh ja ko ru ar hi th de fr es pt
方法: Unicode 脚本归类 + 字符 n-gram(1~3) 朴素贝叶斯
无任何第三方依赖。
"""

from __future__ import annotations

import json
import math
import os
import re
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------- 常量/阈值

SUPPORTED_LANGUAGES = ["en", "zh", "ja", "ko", "ru", "ar", "hi", "th",
                       "de", "fr", "es", "pt"]

# 拒答参数(详见 README "阈值与拒答")
MIN_LETTERS = 4                 # 少于 4 个字母/汉字 -> 证据不足, 拒答
CONF_FLOOR_LONG = 0.50          # >=40 个字符: 后验概率最低要求
CONF_FLOOR_MID = 0.70           # 15~39 字符
CONF_FLOOR_SHORT = 0.90         # 4~14 字符
AMBIG_MARGIN = 0.12             # 前两名概率差 < 0.12 且 top1 < 0.85 -> 歧义拒答
MIXED_MIN_SHARE = 0.05          # 次要语言占比 >= 5% 才报为混入
MINOR_MIN_TOKENS = 3             # 次要语言至少 3 个词/字, 避免噪声

# 置信度校准: 短文本后验向均匀分布收缩, 候选越多收缩越强
# p' = (p + a/K) / (1 + a),  a = CALIB_A * (CALIB_N0 - n) / CALIB_N0
CALIB_N0 = 15                 # 字母数 >= 15 不校准
CALIB_A = 1.0

LATIN_LANGS = ["en", "de", "fr", "es", "pt"]
DIRECT_SCRIPT_LANGS = {
    "Cyrillic": "ru",
    "Arabic": "ar",
    "Devanagari": "hi",
    "Thai": "th",
    "Hangul": "ko",
}
SUPPORTED_SCRIPTS = {"Latin", "Han", "Hiragana", "Katakana",
                     "Cyrillic", "Arabic", "Devanagari", "Thai", "Hangul"}

SMOOTH_K = 0.1                  # add-k 平滑
NGRAM_ORDERS = (1, 2, 3)

# ---------------------------------------------------------------- 脚本归类


def _script(ch: str) -> str:
    cp = ord(ch)
    if 0x3400 <= cp <= 0x4DBF or 0x4E00 <= cp <= 0x9FFF or \
       0xF900 <= cp <= 0xFAFF or 0x20000 <= cp <= 0x2A6DF:
        return "Han"
    if 0x3040 <= cp <= 0x309F:
        return "Hiragana"
    if 0x30A0 <= cp <= 0x30FF or 0x31F0 <= cp <= 0x31FF:
        return "Katakana"
    if 0x1100 <= cp <= 0x11FF or 0xAC00 <= cp <= 0xD7AF:
        return "Hangul"
    if 0x0400 <= cp <= 0x052F:
        return "Cyrillic"
    if 0x0600 <= cp <= 0x06FF or 0x0750 <= cp <= 0x077F or \
       0xFB50 <= cp <= 0xFDFF or 0xFE70 <= cp <= 0xFEFF:
        return "Arabic"
    if 0x0900 <= cp <= 0x097F:
        return "Devanagari"
    if 0x0E00 <= cp <= 0x0E7F:
        return "Thai"
    if 0x0041 <= cp <= 0x005A or 0x0061 <= cp <= 0x007A or \
       0x00C0 <= cp <= 0x024F or 0x1E00 <= cp <= 0x1EFF:
        return "Latin"
    if ch.isalpha():
        return "OtherScript"
    return "Other"


def _is_letter(ch: str) -> bool:
    return _script(ch) not in ("Other",)


def _letters(text: str) -> List[str]:
    return [ch for ch in text if _is_letter(ch)]


# ---------------------------------------------------------------- n-gram 特征

def _words(text: str) -> List[str]:
    """按脚本切段后取字母词; Han/Kana 每字独立成段。"""
    out: List[str] = []
    cur = []
    cur_script: Optional[str] = None
    for ch in text:
        sc = _script(ch)
        if sc in ("Other",):
            if cur:
                out.extend(_flush(cur, cur_script))
                cur, cur_script = [], None
            continue
        if sc in ("Han", "Hiragana", "Katakana"):
            if cur:
                out.extend(_flush(cur, cur_script))
                cur, cur_script = [], None
            out.append(ch.lower())
            continue
        if sc == cur_script or cur_script is None:
            cur.append(ch.lower())
            cur_script = sc
        else:
            out.extend(_flush(cur, cur_script))
            cur, cur_script = [ch.lower()], sc
    if cur:
        out.extend(_flush(cur, cur_script))
    return out


def _flush(chars: List[str], sc: Optional[str]) -> List[str]:
    if not chars:
        return []
    word = "".join(chars)
    if sc == "Latin" or sc in DIRECT_SCRIPT_LANGS:
        return [word]
    return list(word)


def _word_ngrams(word: str):
    s = "^" + word + "$"
    for n in NGRAM_ORDERS:
        if len(s) < n:
            continue
        for i in range(len(s) - n + 1):
            yield s[i:i + n]


def _count_ngrams(text: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for word in _words(text):
        for ng in _word_ngrams(word):
            counts[ng] = counts.get(ng, 0) + 1
    return counts


# ---------------------------------------------------------------- 模型

class Identifier:
    """字符 n-gram 朴素贝叶斯语言识别器。"""

    def __init__(self, profiles: Dict[str, dict]):
        self.profiles = profiles

    @classmethod
    def train(cls, corpora: Dict[str, str]) -> "Identifier":
        profiles: Dict[str, dict] = {}
        for lang, text in corpora.items():
            counts = _count_ngrams(text)
            profiles[lang] = {
                "counts": counts,
                "total": sum(counts.values()),
                "vocab": len(counts),
            }
        return cls(profiles)

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.profiles, f, ensure_ascii=False)

    @classmethod
    def load(cls, path: Optional[str] = None) -> "Identifier":
        if path is None:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "profiles.json")
        with open(path, "r", encoding="utf-8") as f:
            return cls(json.load(f))

    # ------------------------------------------------ scoring

    def _log_prob(self, lang: str, ng: str) -> float:
        prof = self.profiles[lang]
        c = prof["counts"].get(ng, 0)
        return math.log(c + SMOOTH_K) - math.log(
            prof["total"] + SMOOTH_K * prof["vocab"])

    def _score(self, ngrams: Dict[str, int], candidates: List[str]) -> Dict[str, float]:
        total_logp = {lang: 0.0 for lang in candidates}
        for ng, freq in ngrams.items():
            for lang in candidates:
                total_logp[lang] += self._log_prob(lang, ng) * freq
        return total_logp

    @staticmethod
    def _posterior(scores: Dict[str, float]) -> List[Tuple[str, float]]:
        m = max(scores.values())
        exps = {lang: math.exp(v - m) for lang, v in scores.items()}
        z = sum(exps.values())
        return sorted(((lang, exps[lang] / z) for lang in exps),
                      key=lambda kv: -kv[1])

    @staticmethod
    def _calibrate(ranked: List[Tuple[str, float]], n_letters: int):
        k = len(ranked)
        if k <= 1 or n_letters >= CALIB_N0:
            return ranked
        a = CALIB_A * (CALIB_N0 - n_letters) / CALIB_N0
        return [(lang, (p + a / k) / (1 + a)) for lang, p in ranked]

    # ------------------------------------------------ candidates by script

    @staticmethod
    def _script_hist(text: str) -> Dict[str, int]:
        hist: Dict[str, int] = {}
        for ch in text:
            sc = _script(ch)
            if sc != "Other":
                hist[sc] = hist.get(sc, 0) + 1
        return hist

    def _candidates(self, hist: Dict[str, int]) -> Tuple[List[str], Optional[str]]:
        total = sum(hist.values())
        direct: List[str] = []
        latin_ratio = hist.get("Latin", 0) / total
        han_ratio = (hist.get("Han", 0) + hist.get("Hiragana", 0)
                     + hist.get("Katakana", 0)) / total
        kana = hist.get("Hiragana", 0) + hist.get("Katakana", 0)
        for sc, lang in DIRECT_SCRIPT_LANGS.items():
            if hist.get(sc, 0) / total >= 0.6:
                direct.append(lang)
        if han_ratio >= 0.6:
            return (["ja", "zh"] if kana == 0 else ["ja", "zh"]), None
        if latin_ratio >= 0.6:
            return list(LATIN_LANGS), None
        if direct:
            return direct, None
        # 支持脚本占比不足
        supported = sum(v for k, v in hist.items() if k in SUPPORTED_SCRIPTS)
        if supported / total < 0.8:
            return [], "unsupported_script"
        if latin_ratio >= 0.3 or han_ratio >= 0.3:
            cands = list(LATIN_LANGS)
            if han_ratio >= 0.3:
                cands += ["ja", "zh"]
            return cands, None
        return [], "unsupported_script"

    # ------------------------------------------------ segmentation

    @staticmethod
    def _family(sc: str) -> Optional[str]:
        if sc in ("Han", "Hiragana", "Katakana"):
            return "CJK"
        if sc == "Latin":
            return "Latin"
        if sc in DIRECT_SCRIPT_LANGS:
            return sc
        return None

    def _segment_runs(self, text: str) -> List[Tuple[str, str]]:
        runs: List[Tuple[str, str]] = []
        cur: List[str] = []
        cur_fam: Optional[str] = None
        for ch in text:
            fam = self._family(_script(ch))
            if fam is None:
                if cur:
                    cur.append(ch)          # 保留空格/标点以维持分词
                continue
            if cur_fam is None or fam == cur_fam:
                cur.append(ch)
                cur_fam = fam
            else:
                runs.append((cur_fam, "".join(cur)))
                cur, cur_fam = [ch], fam
        if cur:
            runs.append((cur_fam, "".join(cur)))
        # 拉丁片段再按句读切分, 使拉丁语系内部混排(如 fr+de)可分段判定
        final: List[Tuple[str, str]] = []
        for fam, seg in runs:
            if fam != "Latin":
                final.append((fam, seg))
                continue
            for piece in re.split(r"[.!?;:\u3002\uff01\uff1f\n\r]+", seg):
                if not piece.strip():
                    continue
                n_letters = sum(1 for c in piece if _script(c) == "Latin")
                if n_letters < MIN_LETTERS and final and \
                        final[-1][0] == "Latin":
                    pf, pseg = final[-1]
                    final[-1] = (pf, pseg + ". " + piece)
                else:
                    final.append((fam, piece))
        return final

    def _classify_run(self, fam: str, seg: str) -> dict:
        n_letters = sum(1 for c in seg if _script(c) != "Other")
        if fam == "CJK":
            kana = any(_script(c) in ("Hiragana", "Katakana") for c in seg)
            if kana:
                ranked = [("ja", 1.0), ("zh", 0.0)]
            else:
                ranked = self._posterior(self._score(_count_ngrams(seg),
                                                     ["zh", "ja"]))
                ranked = self._calibrate(ranked, n_letters)
            return {"lang": ranked[0][0], "ranked": ranked,
                    "weight": float(n_letters), "resolved": True}
        if fam in DIRECT_SCRIPT_LANGS:
            lang = DIRECT_SCRIPT_LANGS[fam]
            # 泰文等无空格文字按 字母数/4 折算词数
            weight = max(float(len(seg.split())), n_letters / 4.0)
            return {"lang": lang, "ranked": [(lang, 1.0)],
                    "weight": weight, "resolved": True}
        # Latin
        weight = float(len(seg.split()))
        if n_letters < MIN_LETTERS:
            return {"lang": None, "ranked": [], "weight": weight,
                    "resolved": False}
        ranked = self._posterior(self._score(_count_ngrams(seg),
                                             list(LATIN_LANGS)))
        ranked = self._calibrate(ranked, n_letters)
        lang = ranked[0][0] if ranked[0][1] >= 0.5 else None
        return {"lang": lang, "ranked": ranked, "weight": weight,
                "resolved": lang is not None}

    # ------------------------------------------------ public API

    def identify(self, text: str) -> dict:
        n_letters = len(_letters(text))
        if n_letters == 0:
            return _result(rejected=True, reason="no_letters",
                           candidates=[], n_letters=0)
        if n_letters < MIN_LETTERS:
            return _result(rejected=True, reason="too_few_letters",
                           candidates=[], n_letters=n_letters)

        hist = self._script_hist(text)
        supported = sum(v for k, v in hist.items() if k in SUPPORTED_SCRIPTS)
        if supported / n_letters < 0.8:
            return _result(rejected=True, reason="unsupported_script",
                           candidates=[], n_letters=n_letters, hist=hist)

        classified = [(fam, self._classify_run(fam, seg))
                      for fam, seg in self._segment_runs(text)]
        resolved_langs = {rc["lang"] for _, rc in classified
                          if rc["lang"] is not None}
        if len(resolved_langs) >= 2:
            return self._identify_mixed(classified, n_letters, hist)
        return self._identify_single(text, classified, n_letters, hist)

    def _identify_single(self, text, classified, n_letters, hist):
        fam = classified[0][0]
        rc = classified[0][1]
        if fam == "Latin" and not rc["resolved"]:
            candidates, _ = self._candidates(hist)
            ranked = self._posterior(self._score(_count_ngrams(text),
                                                 candidates))
            ranked = self._calibrate(ranked, n_letters)
        elif fam == "Latin":
            ranked = self._posterior(self._score(_count_ngrams(text),
                                                 list(LATIN_LANGS)))
            ranked = self._calibrate(ranked, n_letters)
        else:
            ranked = rc["ranked"]
        top1, conf = ranked[0]
        top2_p = ranked[1][1] if len(ranked) > 1 else 0.0
        reason = self._reject_reason(n_letters, conf, top2_p)
        mixed = self._mix_summary(classified)
        if reason:
            return _result(rejected=True, reason=reason,
                           candidates=ranked, n_letters=n_letters,
                           hist=hist, mixed=mixed, confidence=conf)
        return _result(rejected=False, reason=None, candidates=ranked,
                       language=top1, confidence=conf,
                       n_letters=n_letters, hist=hist, mixed=mixed)

    @staticmethod
    def _reject_reason(n_letters: int, conf: float, top2_p: float):
        floor = (CONF_FLOOR_SHORT if n_letters < 15
                 else CONF_FLOOR_MID if n_letters < 40
                 else CONF_FLOOR_LONG)
        if conf < floor:
            return "low_confidence"
        if conf < 0.85 and conf - top2_p < AMBIG_MARGIN:
            return "ambiguous_top2"
        return None

    def _identify_mixed(self, classified, n_letters, hist):
        latin_votes: Dict[str, float] = {}
        for _, rc in classified:
            if rc["lang"] in LATIN_LANGS:
                latin_votes[rc["lang"]] = \
                    latin_votes.get(rc["lang"], 0.0) + rc["weight"]
        latin_main = max(latin_votes, key=latin_votes.get) if latin_votes else None
        for _, rc in classified:            # 过短拉丁片段归入全文拉丁主语言
            if not rc["resolved"] and latin_main is not None:
                rc["lang"] = latin_main
                rc["ranked"] = [(latin_main, 0.5)]
                rc["resolved"] = True

        share: Dict[str, float] = {}
        total_w = sum(rc["weight"] for _, rc in classified) or 1.0
        for _, rc in classified:
            if rc["lang"] is not None:
                share[rc["lang"]] = share.get(rc["lang"], 0.0) + rc["weight"]
        ranked = sorted(((lang, w / total_w) for lang, w in share.items()),
                        key=lambda kv: -kv[1])
        top1, top_share = ranked[0]
        top2_share = ranked[1][1] if len(ranked) > 1 else 0.0
        # 主语言置信度 = 判定为各主语言片段的加权平均置信度
        num = den = 0.0
        for _, rc in classified:
            if rc["lang"] == top1:
                num += rc["weight"] * rc["ranked"][0][1]
                den += rc["weight"]
        main_conf = num / den if den else 0.0

        mixed = self._mix_summary(classified)
        reason = self._reject_reason(n_letters, main_conf, 1.0 - top_share)
        if reason is None and top_share - top2_share < AMBIG_MARGIN:
            reason = "ambiguous_top2"
        if reason:
            return _result(rejected=True, reason=reason,
                           candidates=ranked, n_letters=n_letters,
                           hist=hist, mixed=mixed, confidence=main_conf)
        return _result(rejected=False, reason=None, candidates=ranked,
                       language=top1, confidence=main_conf,
                       n_letters=n_letters, hist=hist, mixed=mixed)

    # ------------------------------------------------ mixed language

    def _mix_summary(self, classified) -> dict:
        share: Dict[str, float] = {}
        total_w = sum(rc["weight"] for _, rc in classified) or 1.0
        for _, rc in classified:
            if rc["lang"] is not None:
                share[rc["lang"]] = share.get(rc["lang"], 0.0) + rc["weight"]
        parts = sorted(((lang, w / total_w, w) for lang, w in share.items()),
                       key=lambda t: -t[1])
        main = parts[0][0] if parts else None
        minors = [{"lang": lang, "share": round(p, 4), "tokens": round(tw, 1)}
                  for lang, p, tw in parts[1:]
                  if p >= MIXED_MIN_SHARE and tw >= MINOR_MIN_TOKENS]
        return {
            "main": main,
            "parts": [{"lang": lang, "share": round(p, 4)}
                      for lang, p, _ in parts],
            "mixed_in": minors,
            "is_mixed": bool(minors),
        }

    def _analyze_mixed(self, text: str, hist: Dict[str, int]) -> dict:
        classified = [(fam, self._classify_run(fam, seg))
                      for fam, seg in self._segment_runs(text)]
        return self._mix_summary(classified)

def _result(rejected, reason, candidates, language=None, confidence=0.0,
            n_letters=0, hist=None, mixed=None):
    return {
        "language": None if rejected else language,
        "rejected": rejected,
        "reason": reason,
        "confidence": round(confidence, 4),
        "candidates": [[lang, round(p, 4)] for lang, p in candidates],
        "letter_count": n_letters,
        "script_hist": hist or {},
        "mixed": mixed,
    }


if __name__ == "__main__":
    import sys
    _idf = Identifier.load()
    _texts = sys.argv[1:] or [ln.rstrip("\n") for ln in sys.stdin if ln.strip()]
    for _t in _texts:
        print(json.dumps(_idf.identify(_t), ensure_ascii=False))
