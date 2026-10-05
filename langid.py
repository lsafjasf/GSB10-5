"""langid.py -- 纯标准库文本语言识别库 (Python 3.8+)

判定策略
--------
1. 按 Unicode 区块把"有效字符"(字母/表意文字, 不含数字、标点、符号、空白)
   划分到文字系统 (script): han/hiragana/katakana/hangul/cyrillic/arabic/
   devanagari/thai/greek/hebrew/latin。
2. 文字系统能唯一确定语言的 (ko/ar/hi/th/el/he) 直接判定;
   han+假名共存判 ja, 纯 han 判 zh; 西里尔字母按乌克兰语特有字母 (і ї є ґ)
   区分 uk/ru。
3. 拉丁字母文本 (en/de/fr/es/pt/it) 用字符 n-gram (n=1..3) 频率画像 +
   余弦相似度 + softmax 得到候选语言概率分布。
4. 混排: 按文字系统切段; 拉丁段再按句子切分逐句判定, 统计各语言有效字符
   占比, 输出主语言 + 混入语言比例。

拒答条件 (见 README.md 的完整说明)
----------------------------------
- 有效字符数 < MIN_INFORMATIVE_CHARS  -> reason="too_short"
- 有效字符数 == 0                      -> reason="no_linguistic_content"
- 主语言置信度 < MIN_CONFIDENCE        -> reason="low_confidence"
- 拉丁语言 top1-top2 间隔 < MIN_MARGIN 且 top1 < MARGIN_SAFE_CONF
                                         -> reason="ambiguous"
被拒答时 language=None, 但仍返回 candidates 供参考。
"""

import json
import math
import os
import re
from collections import Counter

# ---------------- 阈值常量 (可调) ----------------
MIN_INFORMATIVE_CHARS = 8      # 有效字符少于此值 -> 拒答 (too_short)
MIN_CONFIDENCE = 0.60          # 主语言置信度低于此值 -> 拒答 (low_confidence)
MIN_MARGIN = 0.15              # 拉丁语言 top1-top2 最小间隔
MARGIN_SAFE_CONF = 0.85        # top1 高于此值时豁免间隔检查
MIXED_MIN_RATIO = 0.10         # 占比达到此值才列入混排成分
MIN_SEGMENT_CHARS = 4          # 拉丁句子短于此值不参与 n-gram 判定
SOFTMAX_TEMPERATURE = 0.20     # 拉丁语言 softmax 温度
PROFILE_NGRAMS = (1, 2, 3, 4)  # 字符 n-gram 阶数
PROFILE_SIZE = 800             # 每种语言保留的高频 n-gram 数

LANG_NAMES = {
    "en": "English", "de": "German", "fr": "French", "es": "Spanish",
    "pt": "Portuguese", "it": "Italian", "zh": "Chinese", "ja": "Japanese",
    "ko": "Korean", "ru": "Russian", "uk": "Ukrainian", "ar": "Arabic",
    "hi": "Hindi", "th": "Thai", "el": "Greek", "he": "Hebrew",
}
LATIN_LANGS = ["en", "de", "fr", "es", "pt", "it"]

# 文字系统 -> 唯一语言 (拉丁/西里尔/汉字系需要进一步细分, 不在此表)
SCRIPT_TO_LANG = {
    "hangul": "ko", "arabic": "ar", "devanagari": "hi",
    "thai": "th", "greek": "el", "hebrew": "he",
}

_UK_SPECIFIC = set("іїєґІЇЄҐ")
_SENT_SPLIT = re.compile(r"[.!?\u3002\uff01\uff1f\u2026]+")


def script_of(ch):
    """返回字符所属文字系统; 非字母/表意字符返回 None。"""
    cp = ord(ch)
    if 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF or 0xF900 <= cp <= 0xFAFF:
        return "han"
    if 0x3040 <= cp <= 0x309F:
        return "hiragana"
    if 0x30A0 <= cp <= 0x30FF or 0x31F0 <= cp <= 0x31FF:
        return "katakana"
    if 0xAC00 <= cp <= 0xD7AF or 0x1100 <= cp <= 0x11FF or 0x3130 <= cp <= 0x318F:
        return "hangul"
    if 0x0400 <= cp <= 0x04FF:
        return "cyrillic"
    if 0x0600 <= cp <= 0x06FF or 0x0750 <= cp <= 0x077F or 0xFB50 <= cp <= 0xFDFF:
        return "arabic"
    if 0x0900 <= cp <= 0x097F:
        return "devanagari"
    if 0x0E00 <= cp <= 0x0E7F:
        return "thai"
    if 0x0370 <= cp <= 0x03FF or 0x1F00 <= cp <= 0x1FFF:
        return "greek"
    if 0x0590 <= cp <= 0x05FF or 0xFB1D <= cp <= 0xFB4F:
        return "hebrew"
    if ch.isalpha():
        return "latin"
    return None


def _latin_ngrams(text):
    """提取拉丁字符 n-gram 计数 (非拉丁字符视为空格)。"""
    chars = []
    for ch in text.lower():
        chars.append(ch if script_of(ch) == "latin" else " ")
    s = re.sub(r"\s+", " ", "".join(chars))
    counts = Counter()
    for n in PROFILE_NGRAMS:
        for i in range(len(s) - n + 1):
            gram = s[i:i + n]
            if gram.strip():
                counts[gram] += 1
    return counts


def build_profile(text):
    """从训练文本构建归一化 n-gram 频率画像 (top PROFILE_SIZE)。"""
    counts = _latin_ngrams(text)
    top = counts.most_common(PROFILE_SIZE)
    total = sum(c for _, c in top) or 1
    return {g: c / total for g, c in top}


_LOG_FLOOR = math.log(1e-6)  # 画像外 n-gram 的平滑对数概率


def _avg_loglik(counts, profile):
    """文本 n-gram 计数在画像下的平均每 gram 对数似然 (朴素贝叶斯)。"""
    total = sum(counts.values())
    if not total or not profile:
        return -20.0
    s = 0.0
    for g, c in counts.items():
        w = profile.get(g)
        s += c * (math.log(w) if w else _LOG_FLOOR)
    return s / total


def _softmax(scores, temperature):
    best = max(scores.values())
    exps = {k: math.exp((v - best) / temperature) for k, v in scores.items()}
    z = sum(exps.values()) or 1.0
    return {k: v / z for k, v in exps.items()}


def _script_group(ch):
    """文字系统分组: 汉字系(han/kana)归为 cjk, 其余与 script_of 同名。"""
    sc = script_of(ch)
    if sc is None:
        return None
    return "cjk" if sc in ("han", "hiragana", "katakana") else sc


def _split_blocks(text):
    """按文字系统分组切块; 非有效字符粘附在当前块内, 不打断句子。"""
    blocks = []
    group, chars = None, []
    for ch in text:
        g = _script_group(ch)
        if g is None:
            if chars:
                chars.append(ch)
        elif g == group:
            chars.append(ch)
        else:
            if group is not None:
                blocks.append((group, "".join(chars)))
            group, chars = g, [ch]
    if group is not None:
        blocks.append((group, "".join(chars)))
    return blocks


class Detector:
    """语言识别器。用法: Detector().detect(text) -> dict"""

    def __init__(self, profiles_path=None):
        if profiles_path is None:
            profiles_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "profiles.json")
        with open(profiles_path, "r", encoding="utf-8") as f:
            self.profiles = json.load(f)

    # ---- 拉丁语言判定 ----
    def _detect_latin(self, text):
        counts = _latin_ngrams(text)
        scores = {lang: _avg_loglik(counts, self.profiles[lang])
                  for lang in LATIN_LANGS}
        probs = _softmax(scores, SOFTMAX_TEMPERATURE)
        ranked = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)
        return ranked  # [(lang, prob), ...]

    # ---- 主入口 ----
    def detect(self, text):
        """返回 dict, 字段:
        language   主语言代码, 拒答时为 None
        confidence 主语言置信度 [0,1]
        refused    是否拒答
        reason     拒答原因 (too_short/no_linguistic_content/low_confidence/ambiguous)
        candidates [(lang, prob), ...] 候选语言置信度分布 (至多5个)
        mixed      是否多语言混排
        composition {lang: 占比} 混排时各语言有效字符占比 (>=MIXED_MIN_RATIO)
        """
        result = {
            "language": None, "confidence": 0.0, "refused": False,
            "reason": None, "candidates": [], "mixed": False, "composition": {},
        }
        if not text:
            result.update(refused=True, reason="no_linguistic_content")
            return result

        # 1. 统计各文字系统有效字符数
        script_counts = Counter()
        uk_specific = 0
        for ch in text:
            sc = script_of(ch)
            if sc:
                script_counts[sc] += 1
                if ch in _UK_SPECIFIC:
                    uk_specific += 1
        n_informative = sum(script_counts.values())

        if n_informative == 0:
            result.update(refused=True, reason="no_linguistic_content")
            return result

        # 2. 按文字系统块逐句归属语言, 得到 {lang: 有效字符数}
        lang_chars = Counter()
        conf_num = Counter()   # 置信度分子: 按有效字符数加权
        conf_den = Counter()   # 置信度分母
        latin_candidates = []  # 拉丁文本的候选分布 (用于输出)
        undetermined = 0

        for group, block in _split_blocks(text):
            if group == "cjk":
                # 汉字系按句切分: 含假名的句子 -> ja, 纯汉字句 -> zh
                for sent in _SENT_SPLIT.split(block):
                    n_sent = sum(1 for c in sent
                                 if script_of(c) in ("han", "hiragana", "katakana"))
                    if n_sent == 0:
                        continue
                    has_kana = any(script_of(c) in ("hiragana", "katakana")
                                   for c in sent)
                    lang = "ja" if has_kana else "zh"
                    lang_chars[lang] += n_sent
                    conf_num[lang] += 0.98 * n_sent
                    conf_den[lang] += n_sent
            elif group == "cyrillic":
                # 按句切分: 句内含乌克兰语特有字母 (і ї є ґ) -> uk, 否则 -> ru
                for sent in _SENT_SPLIT.split(block):
                    n_sent = sum(1 for c in sent if script_of(c) == "cyrillic")
                    if n_sent == 0:
                        continue
                    lang = ("uk" if any(c in _UK_SPECIFIC for c in sent)
                            else "ru")
                    lang_chars[lang] += n_sent
                    conf_num[lang] += 0.95 * n_sent
                    conf_den[lang] += n_sent
            elif group == "latin":
                for sent in _SENT_SPLIT.split(block):
                    n_sent = sum(1 for c in sent if script_of(c) == "latin")
                    if n_sent == 0:
                        continue
                    if n_sent < MIN_SEGMENT_CHARS:
                        undetermined += n_sent
                        continue
                    ranked = self._detect_latin(sent)
                    latin_candidates = ranked
                    top_lang, top_prob = ranked[0]
                    lang_chars[top_lang] += n_sent
                    conf_num[top_lang] += top_prob * n_sent
                    conf_den[top_lang] += n_sent
            else:
                lang = SCRIPT_TO_LANG[group]
                n = sum(1 for c in block if script_of(c) == group)
                lang_chars[lang] += n
                conf_num[lang] += 0.98 * n
                conf_den[lang] += n

        seg_conf = {lang: conf_num[lang] / conf_den[lang] for lang in conf_den}

        determined = sum(lang_chars.values())
        if determined == 0:
            # 全是过短的拉丁碎片, 无法判定
            result.update(refused=True, reason="too_short")
            return result

        # 3. 汇总成分与主语言
        composition = {lang: c / determined for lang, c in lang_chars.items()
                       if c / determined >= MIXED_MIN_RATIO}
        main_lang = max(lang_chars.items(), key=lambda kv: kv[1])[0]
        confidence = seg_conf.get(main_lang, 0.5)
        mixed = len(composition) > 1

        # 候选分布: 拉丁语言用 softmax 概率, 其余按字符占比
        if latin_candidates and not mixed:
            cand = latin_candidates[:5]
        else:
            total = sum(lang_chars.values())
            cand = sorted(((l, c / total) for l, c in lang_chars.items()),
                          key=lambda kv: kv[1], reverse=True)[:5]
        result["candidates"] = [[l, round(p, 4)] for l, p in cand]
        result["mixed"] = mixed
        result["composition"] = {l: round(r, 4) for l, r in
                                 sorted(composition.items(), key=lambda kv: -kv[1])}
        if undetermined:
            result["composition"]["undetermined"] = round(
                undetermined / (determined + undetermined), 4)

        # 4. 阈值与拒答判定
        if n_informative < MIN_INFORMATIVE_CHARS:
            result.update(refused=True, reason="too_short",
                          confidence=round(confidence, 4))
            return result
        if confidence < MIN_CONFIDENCE:
            result.update(refused=True, reason="low_confidence",
                          confidence=round(confidence, 4))
            return result
        if main_lang in LATIN_LANGS and len(latin_candidates) > 1:
            top1, top2 = latin_candidates[0][1], latin_candidates[1][1]
            if top1 - top2 < MIN_MARGIN and top1 < MARGIN_SAFE_CONF:
                result.update(refused=True, reason="ambiguous",
                              confidence=round(confidence, 4))
                return result

        result.update(language=main_lang, confidence=round(confidence, 4))
        return result


def detect(text):
    """便捷函数: 使用默认 Detector 单例。"""
    global _DEFAULT
    try:
        det = _DEFAULT
    except NameError:
        det = _DEFAULT = Detector()
    return det.detect(text)


if __name__ == "__main__":
    import sys
    d = Detector()
    for line in sys.stdin:
        line = line.rstrip("\n")
        print(json.dumps(d.detect(line), ensure_ascii=False))
