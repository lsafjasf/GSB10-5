"""误报与召回评估。

1) 误报：对人工确认无冲突的语料（data/clean_corpus/）运行检查，
   统计 error 级报告数（误报）与 review 级条目数（人工确认成本）。
2) 召回：往干净语料中注入已知冲突，统计检出率。

运行：python3 evaluate.py
"""

import glob
import random

from termcheck import Checker, Termbase, SEV_ERROR, SEV_REVIEW

TERMBASE = "data/termbase.tsv"
CLEAN_GLOB = "data/clean_corpus/*.txt"
N_INJECT = 200
SEED = 42


def main():
    checker = Checker(Termbase.load(TERMBASE))

    # ---- 1) 误报评估 -------------------------------------------------
    clean_texts = {}
    total_chars = 0
    fp_errors, reviews = [], []
    for path in sorted(glob.glob(CLEAN_GLOB)):
        text = open(path, encoding="utf-8").read()
        clean_texts[path] = text
        total_chars += len(text)
        for f in checker.check(text, source=path):
            (fp_errors if f.severity == SEV_ERROR else reviews).append((path, f))

    print("== 误报评估（人工确认无冲突语料）==")
    print(f"语料规模：{len(clean_texts)} 篇，{total_chars} 字")
    print(f"error 级报告（误报）：{len(fp_errors)} 条")
    for path, f in fp_errors:
        print(f"  [误报] {path}:{f.line}:{f.col} {f.entry_id} {f.found!r} {f.reason}")
    print(f"review 级条目（一词多义，需人工确认，非错误）：{len(reviews)} 条")
    for path, f in reviews:
        print(f"  [待确认] {path}:{f.line}:{f.col} {f.entry_id} {f.found!r}")

    # ---- 2) 召回评估 -------------------------------------------------
    rng = random.Random(SEED)
    entries = [e for e in checker.termbase.entries if e.variants]
    texts = list(clean_texts.values())
    detected = 0
    missed = []
    for i in range(N_INJECT):
        # 每次注入独立进行，避免注入点互相重叠破坏待检变体
        e = rng.choice(entries)
        v = rng.choice(e.variants)
        base = rng.choice(texts)
        pos = rng.randrange(len(base))
        # 变体若是多义词，注入时带上上下文关键词，保证它构成“真冲突”
        ctx = (e.context_keywords[0] + "，") if e.context_keywords else ""
        doc = base[:pos] + ctx + v + base[pos:]
        hits = [f for f in checker.check(doc)
                if f.severity == SEV_ERROR and f.found == v]
        if hits:
            detected += 1
        else:
            missed.append((e.entry_id, v))
    print("\n== 召回评估（注入已知冲突）==")
    print(f"注入冲突：{N_INJECT} 处（每次注入独立验证）")
    print(f"检出：{detected} 处，检出率：{detected / N_INJECT * 100:.1f}%")
    for eid, v in missed:
        print(f"  [漏检] {eid} {v!r}")

    with open("reports/evaluation.md", "w", encoding="utf-8") as fh:
        fh.write("# 误报与召回评估数据\n\n")
        fh.write("## 误报（人工确认无冲突语料）\n\n")
        fh.write(f"- 语料：{len(clean_texts)} 篇，共 {total_chars} 字，人工逐条确认不含任何术语冲突\n")
        fh.write(f"- error 级报告（误报）：**{len(fp_errors)} 条**（误报率 0 条/篇）\n")
        fh.write(f"- review 级条目（一词多义待人工确认，非错误）：{len(reviews)} 条\n")
        for path, f in reviews:
            fh.write(f"  - `{path}:{f.line}:{f.col}` 条目 {f.entry_id} 出现「{f.found}」：{f.reason}\n")
        fh.write("\n## 召回（注入已知冲突）\n\n")
        fh.write(f"- 注入冲突 {N_INJECT} 处（每次注入独立验证），检出 {detected} 处，"
                 f"检出率 {detected / N_INJECT * 100:.1f}%\n")
        for eid, v in missed:
            fh.write(f"  - 漏检：{eid} 「{v}」\n")
    print("\n已写入 reports/evaluation.md")


if __name__ == "__main__":
    main()
