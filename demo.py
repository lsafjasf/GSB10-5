"""demo.py —— 两套口径在同一样本上的对照输出。

运行：python3 demo.py
"""

import wordfreq
from wordfreq import ASCII_SPLIT, UNICODE_COMPOUND, count_tokens, format_table, tokenize

SAMPLES = {
    "纯英文（大小写/连字符/撇号）":
        "The state-of-the-art System is STATE-OF-THE-ART. Don't panic, don't Panic!",
    "纯中文（含中文标点）":
        "词频统计，词频统计！统计的口径要统一；口径不统一，结果就对不上。",
    "中英混排（边界不得粘连）":
        "我用Python3写了3个word2vec实验，实验结果OK吗？TensorFlow版本是2.0。",
    "大量标点/符号":
        "啊！！！真的吗？？？【震惊】……（无语）——“引号”《书名号》👍🎉 #话题 @某人",
    "Unicode 边角（全角/重音/连字/圈数字/ß）":
        "Ｃａｆé Café ﬁne STRASSE Straße ①② ａ-ｂ well‐known",
}


def show_tokens(text: str) -> None:
    for spec in (ASCII_SPLIT, UNICODE_COMPOUND):
        print("  [%s] %s" % (spec, tokenize(text, spec)))


def show_counts(text: str, limit: int = 12) -> None:
    for spec in (ASCII_SPLIT, UNICODE_COMPOUND):
        counter = count_tokens(text, spec)
        print("  [%s] 总词次=%d 词种数=%d" % (spec, sum(counter.values()), len(counter)))
        for line in format_table(counter, limit).splitlines():
            print("    " + line)


def main() -> None:
    for title, text in SAMPLES.items():
        print("=" * 72)
        print("样本：%s" % title)
        print("原文：%s" % text)
        print("-- 分词序列 --")
        show_tokens(text)
        print("-- 词频（前 12 行） --")
        show_counts(text)
    print("=" * 72)
    print("可复现性自检：同输入同口径跑两次，结果完全一致 ->",
          all(count_tokens(t, s) == count_tokens(t, s)
              for t in SAMPLES.values() for s in (ASCII_SPLIT, UNICODE_COMPOUND)))


if __name__ == "__main__":
    main()
