"""性能基准：合成 10 万条术语 + 100 万字语料，测量耗时与内存峰值。

合成数据尽量贴近真实分布：
  - 70% 单词条、20% 双词条、10% 缩写条；
  - 10% 条目带同义词、5% 带禁用译名、2% 为双义项（带 cue 词）；
  - 语料为中英对照 JSONL，源文随机引用术语（含复数/过去式等变形），
    译文一律使用规范译名 —— 因此基准同时是一次大规模零误报验证。

内存：tracemalloc 峰值（Python 对象）+ resource.ru_maxrss（进程峰值 RSS）。
"""

import json
import random
import resource
import time
import tracemalloc

__all__ = ["run_benchmark", "gen_termbase", "gen_corpus"]

_ZH_BASE = 0x4E00


def _zh_render(i, length=4):
    """由序号生成确定性的唯一中文译名。"""
    chars = []
    x = i
    for _ in range(length):
        chars.append(chr(_ZH_BASE + (x % 0x5000)))
        x = x // 0x5000 + 7
    return "".join(chars)


def gen_termbase(n, seed=42):
    rng = random.Random(seed)
    entries = []
    for i in range(n):
        r = rng.random()
        x, letters = i, ""
        while True:
            letters = chr(ord("a") + x % 26) + letters
            x //= 26
            if not x:
                break
        letters = letters.rjust(4, "a")
        if r < 0.10:
            source = "ZT" + letters.upper()  # 纯大写缩写，如 ZTA、ZTBD
        elif r < 0.30:
            source = "zt" + letters + " alpha"
        else:
            source = "zt" + letters
        entry = {"id": "E%06d" % i, "source": source,
                 "canonical": _zh_render(i)}
        if rng.random() < 0.10:
            entry["synonyms"] = [_zh_render(i, 5)]
        if rng.random() < 0.05:
            entry["forbidden"] = [_zh_render(i, 3)]
        if rng.random() < 0.02:
            entry = {"id": "E%06d" % i, "source": source, "senses": [
                {"id": "s1", "canonical": _zh_render(i),
                 "cues": {"positive": ["alpha"]}},
                {"id": "s2", "canonical": _zh_render(i + n),
                 "cues": {"positive": ["beta"]}},
            ]}
        entries.append(entry)
    return entries


def gen_corpus(entries, total_chars, seed=42):
    """生成中英对照语料，源文+译文总字符数约 total_chars。"""
    rng = random.Random(seed + 1)
    n = len(entries)
    segments = []
    total = 0
    seg_no = 0
    fillers_zh = "的了和是在我们有这个那个可以需要如果因为所以但是"
    while total < total_chars:
        src_parts, tgt_parts = [], []
        for _ in range(4):
            e = entries[rng.randrange(n)]
            if "senses" in e:
                src_parts.append("the %s with alpha marker" % e["source"])
                tgt_parts.append(e["senses"][0]["canonical"])
            else:
                form = e["source"]
                if " " not in form and rng.random() < 0.3 \
                        and form.islower() and form.isalpha():
                    form += rng.choice(["s", "ed", "ing"])
                src_parts.append("the %s works well" % form)
                tgt_parts.append(e["canonical"])
            tgt_parts.append("".join(rng.choice(fillers_zh)
                                     for _ in range(20)))
        src = ". ".join(src_parts) + "."
        tgt = "，".join(tgt_parts) + "。"
        total += len(src) + len(tgt)
        segments.append({"id": "seg%06d" % seg_no,
                         "source": src, "target": tgt})
        seg_no += 1
    return segments


def run_benchmark(entries=100000, chars=1000000, seed=42):
    from .termbase import load
    from .checker import Checker

    t0 = time.perf_counter()
    tb_data = gen_termbase(entries, seed=seed)
    corpus = gen_corpus(tb_data, chars, seed=seed)
    corpus_chars = sum(len(s["source"]) + len(s["target"]) for s in corpus)
    gen_sec = time.perf_counter() - t0

    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    tracemalloc.start()
    tracemalloc.reset_peak()

    t0 = time.perf_counter()
    tb = load(tb_data)
    load_sec = time.perf_counter() - t0
    del tb_data  # 原始 JSON 结构不再需要，避免抬高 RSS 峰值

    t0 = time.perf_counter()
    checker = Checker(tb)
    build_sec = time.perf_counter() - t0

    t0 = time.perf_counter()
    report = checker.check(corpus)
    check_sec = time.perf_counter() - t0

    _, py_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    return {
        "entries": entries,
        "corpus_chars": corpus_chars,
        "corpus_segments": len(corpus),
        "generate_sec": round(gen_sec, 2),
        "load_termbase_sec": round(load_sec, 2),
        "build_matchers_sec": round(build_sec, 2),
        "check_sec": round(check_sec, 2),
        "total_check_pipeline_sec": round(load_sec + build_sec + check_sec, 2),
        "chars_per_sec": int(corpus_chars / max(check_sec, 1e-9)),
        "tracemalloc_peak_mb": round(py_peak / 1024 / 1024, 1),
        "rss_peak_mb(baseline %.0f)" % (rss_before / 1024):
            round(rss_after / 1024, 1),
        "conflicts_found": len(report.conflicts),
        "review_items": len(report.review),
        "termbase_issues": len(report.termbase_issues),
    }


if __name__ == "__main__":
    print(json.dumps(run_benchmark(), ensure_ascii=False, indent=2))
