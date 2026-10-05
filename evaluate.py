"""在 testset.jsonl 上评估: 准确率 / 拒答率 / 置信度分布 / 阈值对照。"""
import json
import os
from collections import Counter, defaultdict

import langid

BASE = os.path.dirname(os.path.abspath(__file__))
EXPECT_REFUSE = {"ultra_short", "digits_symbols"}


def load():
    with open(os.path.join(BASE, "testset.jsonl"), encoding="utf-8") as f:
        return [json.loads(ln) for ln in f]


def run_all(det, items):
    return [(it, det.detect(it["text"])) for it in items]


def is_correct(it, r):
    cat = it["category"]
    if cat in EXPECT_REFUSE:
        return r["refused"]
    if r["refused"]:
        return False
    if cat == "mixed":
        # 50/50 混排时主语言取两者任一均算正确
        main_ok = (r["language"] == it["label"]
                   or (it["ratio"] <= 0.5 and r["language"] == it["label2"]))
        return main_ok and it["label2"] in r.get("composition", {})
    return r["language"] == it["label"]


def report(results, title):
    print("=" * 78)
    print(title)
    print("=" * 78)
    cats = defaultdict(list)
    for it, r in results:
        cats[it["category"]].append((it, r))
    tot_n = tot_ok = tot_ref = 0
    print(f"{'category':<16}{'n':>6}{'answered':>10}{'refused':>9}"
          f"{'rej_rate':>10}{'acc(answered)':>15}{'acc(all)':>10}")
    for cat in ["long", "short", "ultra_short", "mixed", "digits_symbols"]:
        rows = cats[cat]
        n = len(rows)
        refused = sum(1 for _, r in rows if r["refused"])
        ok = sum(1 for it, r in rows if is_correct(it, r))
        answered_rows = [(it, r) for it, r in rows if not r["refused"]]
        ok_ans = sum(1 for it, r in answered_rows if is_correct(it, r))
        acc_a = ok_ans / len(answered_rows) if answered_rows else float("nan")
        print(f"{cat:<16}{n:>6}{n - refused:>10}{refused:>9}"
              f"{refused / n:>10.1%}{acc_a:>15.1%}{ok / n:>10.1%}")
        tot_n += n
        tot_ok += ok
        tot_ref += refused
    print(f"{'TOTAL':<16}{tot_n:>6}{tot_n - tot_ref:>10}{tot_ref:>9}"
          f"{tot_ref / tot_n:>10.1%}{'':>15}{tot_ok / tot_n:>10.1%}")
    return cats


def per_language(cats):
    print("\n-- 单语言类别 (long+short) 分语言准确率 --")
    stats = defaultdict(lambda: [0, 0, 0])  # n, ok, refused
    for cat in ("long", "short"):
        for it, r in cats[cat]:
            s = stats[it["label"]]
            s[0] += 1
            s[1] += is_correct(it, r)
            s[2] += r["refused"]
    print(f"{'lang':<6}{'n':>6}{'acc(all)':>10}{'rej_rate':>10}")
    for lang in sorted(stats):
        n, ok, ref = stats[lang]
        print(f"{lang:<6}{n:>6}{ok / n:>10.1%}{ref / n:>10.1%}")


def confusion(cats):
    print("\n-- 误识别明细 (long+short, 已作答但答错) --")
    wrong = Counter()
    for cat in ("long", "short"):
        for it, r in cats[cat]:
            if not r["refused"] and r["language"] != it["label"]:
                wrong[(it["label"], r["language"])] += 1
    for (a, b), c in wrong.most_common(15):
        print(f"  {a} -> {b}: {c}")
    if not wrong:
        print("  (无)")


def conf_distribution(results):
    print("\n-- 置信度分布 (long+short+mixed 中已作答的样本) --")
    buckets = defaultdict(lambda: [0, 0])
    for it, r in results:
        if it["category"] in EXPECT_REFUSE or r["refused"]:
            continue
        b = min(int(r["confidence"] * 10), 9)
        buckets[b][0] += 1
        buckets[b][1] += is_correct(it, r)
    print(f"{'confidence':<14}{'count':>8}{'acc':>8}")
    for b in range(10):
        if b in buckets:
            n, ok = buckets[b]
            hi = (b + 1) / 10
            print(f"[{b / 10:.1f}, {hi:.1f})     {n:>8}{ok / n:>8.1%}")


def mixed_detail(cats):
    print("\n-- 混排类别细分 --")
    n = main_ok = both_ok = 0
    ratio_err = []
    for it, r in cats["mixed"]:
        n += 1
        if r["refused"]:
            continue
        if (r["language"] == it["label"]
                or (it["ratio"] <= 0.5 and r["language"] == it["label2"])):
            main_ok += 1
            comp = r.get("composition", {})
            if it["label2"] in comp:
                both_ok += 1
                ratio_err.append(abs(comp[it["label"]] - it["ratio"]))
    print(f"  主语言正确率: {main_ok / n:.1%}  (n={n})")
    print(f"  主语言+混入语言都正确: {both_ok / n:.1%}")
    if ratio_err:
        print(f"  主语言占比估计平均绝对误差: {sum(ratio_err) / len(ratio_err):.3f}")


def threshold_sweep(det, items):
    print("\n-- 阈值对照 (long+short, 调整 MIN_CONFIDENCE) --")
    subset = [it for it in items if it["category"] in ("long", "short")]
    print(f"{'MIN_CONFIDENCE':>16}{'rej_rate':>10}{'acc(answered)':>15}{'acc(all)':>10}")
    saved = langid.MIN_CONFIDENCE
    for thr in (0.0, 0.5, 0.6, 0.7, 0.8, 0.9):
        langid.MIN_CONFIDENCE = thr
        n = ok = ref = ok_ans = ans = 0
        for it in subset:
            r = det.detect(it["text"])
            n += 1
            if r["refused"]:
                ref += 1
                continue
            ans += 1
            good = r["language"] == it["label"]
            ok += good
            ok_ans += good
        print(f"{thr:>16.2f}{ref / n:>10.1%}{ok_ans / ans:>15.2%}{ok / n:>10.2%}")
    langid.MIN_CONFIDENCE = saved


def main():
    det = langid.Detector()
    items = load()
    results = run_all(det, items)
    cats = report(results, "评估结果 (默认阈值 MIN_CONFIDENCE=%.2f, "
                           "MIN_INFORMATIVE_CHARS=%d, MIN_MARGIN=%.2f)"
                  % (langid.MIN_CONFIDENCE, langid.MIN_INFORMATIVE_CHARS,
                     langid.MIN_MARGIN))
    per_language(cats)
    confusion(cats)
    conf_distribution(results)
    mixed_detail(cats)
    threshold_sweep(det, items)


if __name__ == "__main__":
    main()
