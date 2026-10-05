"""wordfreq 自测：python3 -m unittest test_wordfreq -v"""

import unittest
from collections import Counter

import wordfreq
from wordfreq import ASCII_SPLIT, UNICODE_COMPOUND, count_tokens, tokenize


class TestAsciiSplit(unittest.TestCase):
    def test_pure_english_case(self):
        # 大小写归一：lower()；标点全部是分隔符。
        self.assertEqual(
            tokenize("Hello, WORLD! hello"), tokenize("hello world HELLO")
        )
        self.assertEqual(count_tokens("Hello, WORLD! hello"), Counter({"hello": 2, "world": 1}))

    def test_hyphen_is_separator(self):
        # 连字符在 ascii_split 口径下是分隔符。
        self.assertEqual(tokenize("state-of-the-art", ASCII_SPLIT), ["state", "of", "the", "art"])

    def test_apostrophe_is_separator(self):
        self.assertEqual(tokenize("don't"), ["don", "t"])
        self.assertEqual(tokenize("don’t"), ["don", "t"])  # 弯引号同样切分

    def test_underscore_is_separator(self):
        self.assertEqual(tokenize("snake_case"), ["snake", "case"])

    def test_non_ascii_dropped(self):
        # 汉字、带重音字母、全角字符都不是 ASCII 词字符。
        self.assertEqual(tokenize("你好caféＡＢＣ", ASCII_SPLIT), ["caf"])

    def test_numbers_kept(self):
        self.assertEqual(tokenize("a1 b2 300"), ["a1", "b2", "300"])


class TestUnicodeCompound(unittest.TestCase):
    def test_pure_chinese_unigram(self):
        # 纯中文：单字成词，不做词库分词。
        self.assertEqual(tokenize("我爱北京天安门"), list("我爱北京天安门"))
        self.assertEqual(count_tokens("好好学习，天天向上"),
                         Counter({"好": 2, "天": 2, "学": 1, "习": 1, "向": 1, "上": 1}))

    def test_pure_english(self):
        self.assertEqual(tokenize("Hello, WORLD! hello"),
                         ["hello", "world", "hello"])

    def test_mixed_no_gluing(self):
        # 中英混排：汉字与拉丁/数字相邻强制断开，英文不会粘成一整块。
        self.assertEqual(
            tokenize("我用Python3写代码OK吗"),
            ["我", "用", "python3", "写", "代", "码", "ok", "吗"],
        )
        self.assertEqual(
            tokenize("version版本2.0发布"),
            ["version", "版", "本", "2", "0", "发", "布"],
        )

    def test_hyphen_kept_inside_word(self):
        # 连字符两侧都是词字符时保留，并归一为 ASCII '-'。
        self.assertEqual(tokenize("state-of-the-art"), ["state-of-the-art"])
        self.assertEqual(tokenize("well‐known"), ["well-known"])   # U+2010
        self.assertEqual(tokenize("a—b"), ["a-b"])                 # em dash 也算连字符
        self.assertEqual(tokenize("ａ-ｂ"), ["a-b"])                # 全角字母经 NFKC

    def test_hyphen_at_edges_dropped(self):
        self.assertEqual(tokenize("well-"), ["well"])
        self.assertEqual(tokenize("-well"), ["well"])
        self.assertEqual(tokenize("a--b"), ["a--b"])  # 词中连续连字符原样保留

    def test_apostrophe_is_separator(self):
        self.assertEqual(tokenize("don't"), ["don", "t"])
        self.assertEqual(tokenize("it’s"), ["it", "s"])

    def test_casefold(self):
        # casefold 比 lower 更激进：ß → ss。
        self.assertEqual(tokenize("STRASSE Straße"), ["strasse", "strasse"])
        self.assertEqual(tokenize("ＣＡＦÉ Café"), ["café", "café"])

    def test_nfkc(self):
        self.assertEqual(tokenize("ﬁne"), ["fine"])          # 连字 ﬁ → fi
        self.assertEqual(tokenize("①②"), ["12"])        # 圈数字→半角数字，相邻合并
        self.assertEqual(tokenize("１２３abc"), ["123abc"])   # 全角数字+字母连写

    def test_combining_marks(self):
        # 组合重音符号附着在词内：café 两种 Unicode 写法归一结果一致。
        composed = "caf\u00e9"          # é 预组合
        decomposed = "cafe\u0301"       # e + 组合重音
        self.assertEqual(tokenize(composed), tokenize(decomposed))

    def test_punctuation_heavy(self):
        text = "啊！！！真的吗？？？……【震惊】（无语）——“引号”『书名号』《》"
        self.assertEqual(tokenize(text),
                         ["啊", "真", "的", "吗", "震", "惊", "无", "语", "引", "号", "书", "名", "号"])
        self.assertEqual(tokenize(text, ASCII_SPLIT), [])

    def test_emoji_and_symbols_are_separators(self):
        self.assertEqual(tokenize("好👍棒🎉"), ["好", "棒"])
        self.assertEqual(tokenize("a©b®c"), ["a", "b", "c"])

    def test_nfkc_trademark_becomes_letters(self):
        # NFKC 的兼容分解会把 ™ 展开成 TM（词字符），这是口径的一部分。
        self.assertEqual(tokenize("a™b"), ["atmb"])

    def test_decimal_point_splits(self):
        self.assertEqual(tokenize("3.14"), ["3", "14"])

    def test_han_variants(self):
        self.assertEqual(tokenize("〇"), ["〇"])
        self.assertEqual(tokenize("々"), ["々"])
        self.assertEqual(tokenize("𠀀"), ["𠀀"])  # 扩展 B 区汉字


class TestReproducibility(unittest.TestCase):
    SAMPLE = ("我用Python3写state-of-the-art代码，don't 粘贴成一整块！"
              "Ｃａｆé ﬁne ① 测试……测试？测试。")

    def test_same_input_same_output(self):
        for spec in (ASCII_SPLIT, UNICODE_COMPOUND):
            first = tokenize(self.SAMPLE, spec)
            for _ in range(3):
                self.assertEqual(tokenize(self.SAMPLE, spec), first)
                self.assertEqual(count_tokens(self.SAMPLE, spec),
                                 count_tokens(self.SAMPLE, spec))

    def test_table_ordering_deterministic(self):
        c = count_tokens(self.SAMPLE)
        t1 = wordfreq.to_table(c)
        t2 = wordfreq.to_table(count_tokens(self.SAMPLE))
        self.assertEqual(t1, t2)
        # 排序键：(-次数, 词)
        self.assertEqual(t1, sorted(c.items(), key=lambda kv: (-kv[1], kv[0])))

    def test_empty_and_blank(self):
        for spec in (ASCII_SPLIT, UNICODE_COMPOUND):
            self.assertEqual(tokenize("", spec), [])
            self.assertEqual(tokenize("   \n\t。。。", spec), [])
            self.assertEqual(count_tokens("", spec), Counter())

    def test_unknown_spec_rejected(self):
        with self.assertRaises(ValueError):
            tokenize("abc", "no_such_spec")


if __name__ == "__main__":
    unittest.main()
