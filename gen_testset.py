"""生成测试集 testset.jsonl (固定随机种子, 可复现)。

类别:
  long           长文本: 同语言 3-5 句拼接, 120 条/语言
  short          短文本: 单句, 120 条/语言
  ultra_short    超短文本: 1-6 个有效字符的片段, 60 条/语言 (期望拒答)
  mixed          多语言混排: 两种语言按比例拼接 (50/50, 70/30, 90/10), 800 条
  digits_symbols 纯数字/符号/emoji, 200 条 (期望拒答)
"""
import json
import os
import random
import langid

BASE = os.path.dirname(os.path.abspath(__file__))
LANGS = sorted(langid.LANG_NAMES)
MIXED_PAIRS = [("en", "zh"), ("zh", "ja"), ("en", "ru"), ("fr", "ar"),
               ("de", "zh"), ("es", "pt"), ("en", "es"), ("ko", "ja"),
               ("hi", "en"), ("th", "zh"), ("he", "ar"), ("el", "en"),
               ("pt", "fr"), ("uk", "ru"), ("it", "es"), ("ja", "zh")]


def load_pools():
    pools = {}
    for lang in LANGS:
        with open(os.path.join(BASE, "data", "pools", lang + ".txt"),
                  encoding="utf-8") as f:
            pools[lang] = [ln.strip() for ln in f if ln.strip()]
    return pools


def informative_len(text):
    return sum(1 for ch in text if langid.script_of(ch))


def truncate_informative(text, limit):
    out, n = [], 0
    for ch in text:
        if langid.script_of(ch):
            n += 1
            if n > limit:
                break
        out.append(ch)
    return "".join(out).strip()


def main():
    rng = random.Random(42)
    pools = load_pools()
    items = []
    i = 0

    def add(category, text, label, label2=None, ratio=None):
        nonlocal i
        items.append({"id": i, "category": category, "text": text,
                      "label": label, "label2": label2, "ratio": ratio})
        i += 1

    for lang in LANGS:
        sents = pools[lang]
        for _ in range(120):  # long
            add("long", " ".join(rng.sample(sents, rng.randint(3, 5))), lang)
        for _ in range(120):  # short
            add("short", rng.choice(sents), lang)
        for _ in range(60):  # ultra_short
            add("ultra_short",
                truncate_informative(rng.choice(sents), rng.randint(1, 6)), lang)

    for _ in range(800):  # mixed
        la, lb = rng.choice(MIXED_PAIRS)
        target = rng.choice([0.5, 0.7, 0.9])
        a_text = " ".join(rng.sample(pools[la], 3))
        a_len = informative_len(a_text)
        b_need = a_len * (1 - target) / target
        b_parts, b_len = [], 0
        while b_len < b_need:
            s = rng.choice(pools[lb])
            b_parts.append(s)
            b_len += informative_len(s)
        text = a_text + " " + " ".join(b_parts)
        add("mixed", text, la, lb, round(a_len / (a_len + b_len), 3))

    sym_pools = ["0123456789", "!@#$%^&*()", "     ", "3.14159265",
                 "2024-01-01 12:00:00", "+86-138-0000-0000", "...", "???!!!",
                 "😀😂🤣👍🎉", "©®™€£¥$¢", "123 456 789", "#hashtag @user",
                 "0xDEADBEEF", "|||||-----|||||", "1/2 + 3/4 = 5/4",
                 "★☆♠♣♥♦", "Ⅻ Ⅼ Ⅿ", "→←↑↓↔", "0000", "12%"]
    for _ in range(200):
        add("digits_symbols", rng.choice(sym_pools), None)

    out = os.path.join(BASE, "testset.jsonl")
    with open(out, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    print("wrote", out, len(items), "items")


if __name__ == "__main__":
    main()
