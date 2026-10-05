# 对照数据与基准结果

以下数据全部由 `python3 demo.py` 与 `python3 benchmark.py` 实际运行产出，
同环境重跑可逐字节复现（语料 seed 固定为 20261005）。

## 1. 两套口径对照（分词序列 + 词频）

### 1.1 纯英文（大小写 / 连字符 / 撇号）

原文：`The state-of-the-art System is STATE-OF-THE-ART. Don't panic, don't Panic!`

| 口径 | 分词序列 |
| --- | --- |
| ascii_split | `the state of the art system is state of the art don t panic don t panic` |
| unicode_compound | `the state-of-the-art system is state-of-the-art don t panic don t panic` |

| 词 | ascii_split | unicode_compound |
| --- | --- | --- |
| the | 3 | 1 |
| state-of-the-art | — | 2 |
| state / of / art | 2 / 2 / 2 | — |
| don / t | 2 / 2 | 2 / 2 |
| panic | 2 | 2 |
| system / is | 1 / 1 | 1 / 1 |
| 总词次 / 词种数 | 17 / 9 | 11 / 7 |

差异来源：**连字符**。ascii 口径把 `-` 当分隔符，复合词被拆成 4 个词，
且大写形态 `STATE-OF-THE-ART` 归一后与 `the` 等普通词合流，把 `the` 的计数推高到 3；
unicode 口径保留词内连字符，`state-of-the-art` 是一个独立词。

### 1.2 纯中文（含中文标点）

原文：`词频统计，词频统计！统计的口径要统一；口径不统一，结果就对不上。`

| 口径 | 结果 |
| --- | --- |
| ascii_split | **空**（该口径只认 ASCII，汉字全部丢弃） |
| unicode_compound | `词 频 统 计 词 频 统 计 统 计 的 口 径 要 统 一 口 径 不 统 一 结 果 就 对 不 上`（27 词次 / 15 词种） |

unicode_compound 词频（前 8）：`统×5 计×3 一×2 不×2 口×2 径×2 词×2 频×2`。
中文按**单字**统计，不做词库分词（口径声明 2.4）。

### 1.3 中英混排（边界不得粘连）

原文：`我用Python3写了3个word2vec实验，实验结果OK吗？TensorFlow版本是2.0。`

| 口径 | 分词序列 |
| --- | --- |
| ascii_split | `python3 3 word2vec ok tensorflow 2 0`（汉字全丢） |
| unicode_compound | `我 用 python3 写 了 3 个 word2vec 实 验 实 验 结 果 ok 吗 tensorflow 版 本 是 2 0` |

关键点：`Python3`、`word2vec` 是完整的词，**没有**和相邻汉字粘成一整块；
汉字与拉丁/数字相邻处强制断词（口径声明 2.4）。`实验` 计 2 次（`实×2 验×2`）。

### 1.4 大量标点 / 符号

原文：`啊！！！真的吗？？？【震惊】……（无语）——“引号”《书名号》👍🎉 #话题 @某人`

| 口径 | 结果 |
| --- | --- |
| ascii_split | 空 |
| unicode_compound | `啊 真 的 吗 震 惊 无 语 引 号 书 名 号 话 题 某 人`（17 词次 / 16 词种，`号×2`） |

所有中英文标点、Emoji、`#`、`@` 都是分隔符，没有一个进入词。

### 1.5 Unicode 边角（全角 / 重音 / 连字 / 圈数字 / ß）

原文：`Ｃａｆé Café ﬁne STRASSE Straße ①② ａ-ｂ well‐known`

| 口径 | 分词序列 |
| --- | --- |
| ascii_split | `caf ne strasse stra e well known` |
| unicode_compound | `café café fine strasse strasse 12 a-b well-known` |

unicode_compound 词频：`café×2 strasse×2 fine×1 12×1 a-b×1 well-known×1`。

差异来源逐条：
- `Ｃａｆé`：ascii 口径丢非 ASCII → `caf`；unicode 口径 NFKC 全角转半角 → `café`，与 `Café` 归一合并（×2）。
- `ﬁne`：ascii 口径 `ﬁ` 非 ASCII → 只剩 `ne`；unicode 口径 NFKC 拆连字 → `fine`。
- `Straße`：ascii 口径 `ß` 非 ASCII → `stra`+`e`；unicode 口径 casefold → `strasse`，与 `STRASSE` 合并（×2）。
- `①②`：ascii 口径丢弃；unicode 口径 NFKC → `12`。
- `well‐known`（U+2010 连字号）：ascii 口径切成 `well/known`；unicode 口径归一为 `well-known`。

## 2. 边界用例汇总（均有 unittest 断言锁定）

| 输入 | ascii_split | unicode_compound | 说明 |
| --- | --- | --- | --- |
| `don't` / `don’t` | `don` `t` | `don` `t` | 撇号两套口径都切分 |
| `well-` / `-well` | `well` | `well` | 词首词尾连字符丢弃 |
| `a--b` | `a` `b` | `a--b` | 词中连续连字符保留 |
| `snake_case` | `snake` `case` | `snake` `case` | 下划线两套口径都是分隔符 |
| `3.14` / `2.0` | `3` `14` / `2` `0` | 同左 | 小数点是分隔符 |
| `café`(预组合) vs `café`(e+U+0301) | `caf` | 两者同为 `café` | NFC 合成归一 |
| `𠀀`（扩展 B 汉字） | 空 | `𠀀` | 基本区外汉字同样单字成词 |
| `〇` `々` | 空 | `〇` `々` | 汉字数字/叠字符号按汉字处理 |
| `好👍棒🎉` | 空 | `好` `棒` | Emoji 是分隔符 |
| `a™b` | `a` `b` | `atmb` | NFKC 把 ™ 展开为 TM |
| 空串 / 纯空白 / 纯标点 | 空 | 空 | 不产生任何 token |

## 3. 100 万字基准（耗时 + 内存峰值）

环境：Python 3.12.3，Linux 6.18 (WSL2)，AMD Ryzen 7 9700X，16 核，15 GB 内存。
语料：`benchmark.build_corpus(1_000_000)`，seed=20261005 确定性生成，
实际 1,000,020 字符，混合纯中文/纯英文/中英混排/大量标点/Unicode 边角/合成词
（约 1.05 万词种）。计时为分词+统计整体，跑 5 次。

| 口径 | 总词次 | 词种数 | 耗时 min | 耗时 mean | tracemalloc 峰值 |
| --- | --- | --- | --- | --- | --- |
| ascii_split | 134,706 | 10,521 | 0.025 s | 0.056 s | 0.71 MB |
| unicode_compound | 291,880 | 10,404 | 0.287 s | 0.639 s | 7.79 MB |

进程级峰值 RSS（`ru_maxrss`，含解释器与语料字符串本身）：**约 28–32 MB**（随运行在 27.5–31.5 MB 间小幅波动）。

说明：
- mean 明显高于 min 是因为第 1 次运行在 tracemalloc 追踪下进行（有追踪开销），
  min 更接近真实速度；复跑时请以自己的 min 为准。
- unicode_compound 的 tracemalloc 峰值主要来自 NFKC 对整篇语料的一次性拷贝
  （约 100 万字符 × 4 字节 ≈ 4 MB，语料含非 BMP 字符按最宽码元存储）加上 Counter 本身；
  分词是惰性流式的，不会把全部 token 列表留在内存。
- 吞吐：unicode_compound 约 350 万字符/秒，ascii_split 约 4000 万字符/秒（本机 min 值）。

## 4. 可复现性验证

- `test_wordfreq.py` 中 `TestReproducibility`：同输入同口径连续跑 4 次，
  分词序列与 Counter 完全一致；`to_table` 排序键固定为 `(-次数, 词)`。
- `benchmark.py` 每次运行都断言“两次统计结果相等”，不相等直接抛 AssertionError。
- 语料由固定 seed 生成，跨机器字节级一致；不依赖 locale、时区、环境变量。
