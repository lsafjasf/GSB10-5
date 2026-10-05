from __future__ import annotations

import unittest

from wordfreq import LOOSE_V1, UNICODE_V1, count_summary, count_tokens, sorted_counts, tokenize
from wordfreq import TokenizationProfile


class TokenizeTest(unittest.TestCase):
    def test_pure_chinese_uses_one_ideograph_per_token(self) -> None:
        self.assertEqual(
            list(tokenize("中文分词。", UNICODE_V1)),
            ["中", "文", "分", "词"],
        )

    def test_pure_english_profile_difference(self) -> None:
        text = "Hello, HELLO! Well-known well known."
        self.assertEqual(
            list(tokenize(text, UNICODE_V1)),
            ["hello", "hello", "well", "known", "well", "known"],
        )
        self.assertEqual(
            list(tokenize(text, LOOSE_V1)),
            ["Hello", "HELLO", "Well-known", "well", "known"],
        )

    def test_mixed_chinese_english_does_not_glue_english(self) -> None:
        text = "我爱NLP，自然语言处理with AI。"
        self.assertEqual(
            list(tokenize(text, UNICODE_V1)),
            ["我", "爱", "nlp", "自", "然", "语", "言", "处", "理", "with", "ai"],
        )

    def test_punctuation_is_a_boundary_and_is_not_emitted(self) -> None:
        text = "Hello...世界!!!（测试）【边界】——done?"
        self.assertEqual(
            list(tokenize(text, UNICODE_V1)),
            ["hello", "世", "界", "测", "试", "边", "界", "done"],
        )

    def test_hyphen_and_apostrophe_rules(self) -> None:
        text = "well-known state-of-the-art don't rock ’n’ roll"
        self.assertEqual(
            list(tokenize(text, UNICODE_V1)),
            [
                "well",
                "known",
                "state",
                "of",
                "the",
                "art",
                "don't",
                "rock",
                "n",
                "roll",
            ],
        )
        self.assertEqual(
            list(tokenize(text, LOOSE_V1)),
            [
                "well-known",
                "state-of-the-art",
                "don't",
                "rock",
                "n",
                "roll",
            ],
        )

    def test_unicode_normalization_and_casefold(self) -> None:
        self.assertEqual(
            list(tokenize("Ｈｅｌｌｏ　Ｗｏｒｌｄ１２３ Straße", UNICODE_V1)),
            ["hello", "world123", "strasse"],
        )

    def test_numbers_underscores_and_decimals(self) -> None:
        self.assertEqual(
            list(tokenize("abc_def 3.14 2026", UNICODE_V1)),
            ["abc", "def", "3", "14", "2026"],
        )

    def test_empty_and_non_string_input(self) -> None:
        self.assertEqual(list(tokenize("", UNICODE_V1)), [])
        with self.assertRaises(TypeError):
            list(tokenize(None, UNICODE_V1))  # type: ignore[arg-type]


class CountTest(unittest.TestCase):
    def test_counts_are_reproducible(self) -> None:
        text = "Hello, HELLO! 世界。Well-known well-known."
        first = count_summary(text, UNICODE_V1)
        second = count_summary(text, UNICODE_V1)
        self.assertEqual(first, second)
        self.assertEqual(
            first["counts"],
            [
                {"token": "hello", "count": 2},
                {"token": "known", "count": 2},
                {"token": "well", "count": 2},
                {"token": "世", "count": 1},
                {"token": "界", "count": 1},
            ],
        )

    def test_sorted_counts_breaks_ties_by_token(self) -> None:
        self.assertEqual(
            sorted_counts({"b": 2, "a": 2, "c": 1}),
            [("a", 2), ("b", 2), ("c", 1)],
        )

    def test_count_tokens(self) -> None:
        self.assertEqual(
            count_tokens("One one TWO two", UNICODE_V1),
            {"one": 2, "two": 2},
        )

    def test_profile_validation(self) -> None:
        with self.assertRaises(ValueError):
            TokenizationProfile(name="bad", case="fold")


if __name__ == "__main__":
    unittest.main()
