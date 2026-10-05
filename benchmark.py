"""benchmark.py —— 100 万字语料的耗时与内存峰值基准。

运行：python3 benchmark.py [目标字符数，默认 1_000_000]

口径说明：
  - 语料由 random.Random(20261005) 确定性生成，任何机器上字节级一致；
  - 计时：time.perf_counter()，分词+统计整体跑 5 次，报 min/mean；
  - 内存：tracemalloc 峰值（Python 堆内本次统计的净增量）
          + resource.ru_maxrss（进程级峰值 RSS，含解释器自身）。
"""

import gc
import random
import resource
import sys
import time
import tracemalloc

import wordfreq
from wordfreq import ASCII_SPLIT, UNICODE_COMPOUND, count_tokens

SEED = 20261005

# 语料模板池：纯英文 / 纯中文 / 中英混排 / 大量标点 / Unicode 边角
CHUNKS = [
    "the quick brown fox jumps over the lazy dog",
    "state-of-the-art natural language processing pipeline",
    "Don't panic! Don't stop believing, don't look back.",
    "词频统计的口径必须统一，分词大小写标点连字符逐项声明",
    "好好学习天天向上，学而时习之不亦说乎",
    "我用Python3写了3个word2vec实验，TensorFlow版本2.0效果不错",
    "啊！！！真的吗？？？【震惊】……（无语）——“引号”《书名号》",
    "Ｃａｆé Café ﬁne STRASSE Straße ①②③ ａ-ｂ well‐known 👍🎉",
    "数据data科学science混合mixed排版layout测试test",
    "email@example.com https://example.com/path?q=1 #tag @user 3.14",
]
SEPARATORS = [" ", " ", " ", "\n", "，", "。", "！", "；", "——", "…", "  "]


def build_corpus(target_chars: int, seed: int = SEED) -> str:
    """确定性拼装语料。

    65% 片段来自固定模板（覆盖中英混排/标点/Unicode 边角），
    35% 插入合成词 termNNNNN（约 6 万种），让词表规模贴近真实文本，
    内存峰值主要由 Counter 承担而不是只由 NFKC 拷贝承担。
    """
    rng = random.Random(seed)
    parts: list[str] = []
    total = 0
    while total < target_chars:
        if rng.random() < 0.35:
            chunk = "term%05d" % rng.randrange(60000)
        else:
            chunk = rng.choice(CHUNKS)
        sep = rng.choice(SEPARATORS)
        parts.append(chunk)
        parts.append(sep)
        total += len(chunk) + len(sep)
    return "".join(parts)


def measure(text: str, spec: str, repeat: int = 5) -> dict:
    gc.collect()
    tracemalloc.start()
    start = time.perf_counter()
    counter = count_tokens(text, spec)
    elapsed_first = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    times = [elapsed_first]
    for _ in range(repeat - 1):
        start = time.perf_counter()
        again = count_tokens(text, spec)
        times.append(time.perf_counter() - start)
    assert again == counter, "同输入同口径两次结果不一致！"

    return {
        "spec": spec,
        "total_tokens": sum(counter.values()),
        "unique_tokens": len(counter),
        "time_min_s": min(times),
        "time_mean_s": sum(times) / len(times),
        "tracemalloc_peak_mb": peak / 1024 / 1024,
        "top5": wordfreq.to_table(counter)[:5],
    }


def main() -> None:
    target = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000
    corpus = build_corpus(target)
    print("语料：%d 字符（seed=%d，确定性生成）" % (len(corpus), SEED))
    print("Python %s | %s" % (sys.version.split()[0], sys.platform))
    print("-" * 72)
    for spec in (ASCII_SPLIT, UNICODE_COMPOUND):
        result = measure(corpus, spec)
        print("口径 %s" % result["spec"])
        print("  总词次=%d  词种数=%d" % (result["total_tokens"], result["unique_tokens"]))
        print("  耗时 min=%.3fs  mean=%.3fs（5 次）"
              % (result["time_min_s"], result["time_mean_s"]))
        print("  tracemalloc 峰值=%.2f MB（本次统计的 Python 堆净增量）"
              % result["tracemalloc_peak_mb"])
        print("  Top5: %s" % ", ".join("%s×%d" % kv for kv in result["top5"]))
    print("-" * 72)
    print("进程峰值 RSS（ru_maxrss，含解释器与全部常驻对象）: %.2f MB"
          % (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024))


if __name__ == "__main__":
    main()
