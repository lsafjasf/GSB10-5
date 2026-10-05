# 边界用例

以下结果由 `wordfreq.tokenize()` 的规则推出，并由 `tests/test_wordfreq.py` 固定。

| 场景 | 输入 | 口径 | token 结果 |
| --- | --- | --- | --- |
| 纯中文 | `中文分词。` | `unicode-v1` | `中 / 文 / 分 / 词` |
| 纯英文 | `Hello, HELLO! Well-known well known.` | `unicode-v1` | `hello / hello / well / known / well / known` |
| 纯英文 | `Hello, HELLO! Well-known well known.` | `loose-v1` | `Hello / HELLO / Well-known / well / known` |
| 中英混排 | `我爱NLP，自然语言处理with AI。` | `unicode-v1` | `我 / 爱 / nlp / 自 / 然 / 语 / 言 / 处 / 理 / with / ai` |
| 大量标点 | `Hello...世界!!!（测试）【边界】——done?` | `unicode-v1` | `hello / 世 / 界 / 测 / 试 / 边 / 界 / done` |
| 撇号边界 | `well-known state-of-the-art don't rock ’n’ roll` | `unicode-v1` | `well / known / state / of / the / art / don't / rock / n / roll` |
| 撇号边界 | `well-known state-of-the-art don't rock ’n’ roll` | `loose-v1` | `well-known / state-of-the-art / don't / rock / n / roll` |
| Unicode 规范化 | `Ｈｅｌｌｏ　Ｗｏｒｌｄ１２３ Straße` | `unicode-v1` | `hello / world123 / strasse` |
| 数字与下划线 | `abc_def 3.14 2026` | `unicode-v1` | `abc / def / 3 / 14 / 2026` |

说明：`’n’` 中两个弯撇号都不在两个词字符之间，因此只作为边界，独立的 `n` 是一个 token。
