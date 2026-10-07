"""自测脚本: 在留出测试集 + 构造用例上评估准确率/拒答率/置信度分布。

测试集来源与规模(自建):
  - long   : data/*.txt 留出 20% 行(每语言 6 句, 共 72 条), 训练时未见过
  - short  : 上述句子截断到 12 个字符(72 条)
  - tiny   : 上述句子截取前 1~3 个字符(72 条), 期望拒答
  - digits : 纯数字/日期/编号(8 条), 期望拒答
  - symbols: 纯标点符号(8 条), 期望拒答
  - mixed  : 两种语言句子拼接(12 条), 期望主语言正确且报出混入语言
"""
import json
import os
from collections import defaultdict

from langid import Identifier, SUPPORTED_LANGUAGES
from train_profiles import load_split

HERE = os.path.dirname(os.path.abspath(__file__))


def build_cases():
    cases = []
    held = {}
    for lang in SUPPORTED_LANGUAGES:
        _, test = load_split(lang)
        held[lang] = test
        for ln in test:
            cases.append(("long", ln, lang))
            cases.append(("short", ln[:12], lang))
            cases.append(("tiny", ln[:2], None))          # 期望拒答
    for s in ["1234567890", "2024-01-01 12:30:45", "99.9% off!",
              "007 008 009", "3.14159265", "+86 138 0000 0000",
              "1000000", "42"]:
        cases.append(("digits", s, None))
    for s in ["!@#$%^&*()", "?????!!!!!", "-----", "……。。。",
              "★☆♠♣♥♦", "===+++", "©®™§¶", "||\\\\//"]:
        cases.append(("symbols", s, None))
    from langid import _words
    pairs = [("zh", "en"), ("en", "zh"), ("ja", "en"), ("fr", "de"),
             ("ru", "en"), ("es", "pt"), ("ko", "zh"), ("ar", "en"),
             ("de", "fr"), ("hi", "en"), ("th", "en"), ("pt", "es")]
    for i, (main, minor) in enumerate(pairs):
        main_txt = held[main][i % len(held[main])]
        minor_txt = held[minor][(i + 1) % len(held[minor])]
        # 混入片段取主文本词/字数的 1/4(至少 4 个单位), 保证主语言占优
        n_units = max(4, len(_words(main_txt)) // 4)
        minor_part = " ".join(_words(minor_txt)[:n_units])
        cases.append(("mixed", main_txt + " " + minor_part,
                      (main, minor)))
    return cases


def main():
    idf = Identifier.load()
    cases = build_cases()
    stats = defaultdict(lambda: dict(n=0, answered=0, rejected=0,
                                     correct=0, mixed_ok=0))
    reasons = defaultdict(int)
    conf_bins = [(b / 10, (b + 1) / 10) for b in range(10)]
    conf_hist = {"correct": [0] * 10, "wrong": [0] * 10,
                 "rejected": [0] * 10}
    per_lang = defaultdict(lambda: [0, 0])   # long 类: [correct, answered]

    for cat, text, expect in cases:
        r = idf.identify(text)
        st = stats[cat]
        st["n"] += 1
        if r["rejected"]:
            st["rejected"] += 1
            reasons[r["reason"]] += 1
            c = min(max(r["confidence"], 0.0), 0.9999)
            conf_hist["rejected"][int(c * 10)] += 1
            if expect is None:
                st["correct"] += 1          # 期望拒答且确实拒答
            continue
        st["answered"] += 1
        ok = False
        if cat == "mixed":
            main_lang, minor_lang = expect
            mx = r["mixed"] or {}
            minors = {m["lang"] for m in mx.get("mixed_in", [])}
            ok = (r["language"] == main_lang
                  and mx.get("main") == main_lang
                  and minor_lang in minors)
            if ok:
                st["mixed_ok"] += 1
        elif expect is not None:
            ok = r["language"] == expect
            if cat == "long":
                per_lang[expect][1] += 1
                if ok:
                    per_lang[expect][0] += 1
        if ok:
            st["correct"] += 1
        # 置信度分布(仅已作答)
        c = min(max(r["confidence"], 0.0), 0.9999)
        conf_hist["correct" if ok else "wrong"][int(c * 10)] += 1

    report = {"categories": {}, "reject_reasons": dict(reasons),
              "confidence_histogram": {
                  "bins": ["%.1f-%.1f" % ab for ab in conf_bins],
                  **conf_hist},
              "per_language_long": {
                  lang: {"correct": v[0], "answered": v[1]}
                  for lang, v in sorted(per_lang.items())}}

    print(f"{'category':<8} {'n':>4} {'answered':>8} {'rejected':>8} "
          f"{'correct':>7} {'acc(ans)':>9} {'rej_rate':>9}")
    for cat in ["long", "short", "tiny", "digits", "symbols", "mixed"]:
        st = stats[cat]
        acc = st["correct"] / st["answered"] if st["answered"] else 0.0
        rej = st["rejected"] / st["n"]
        report["categories"][cat] = {**st,
                                     "accuracy_on_answered": round(acc, 4),
                                     "rejection_rate": round(rej, 4)}
        print(f"{cat:<8} {st['n']:>4} {st['answered']:>8} {st['rejected']:>8} "
              f"{st['correct']:>7} {acc:>9.2%} {rej:>9.2%}")

    print("\nper-language accuracy (long, answered only):")
    for lang, (c, a) in sorted(per_lang.items()):
        print(f"  {lang}: {c}/{a}")

    print("\nreject reasons:", dict(reasons))
    print("\nconfidence histogram (all items):")
    for i, ab in enumerate(conf_bins):
        print(f"  {ab[0]:.1f}-{ab[1]:.1f}: correct={conf_hist['correct'][i]:>3} "
              f"wrong={conf_hist['wrong'][i]:>3} "
              f"rejected={conf_hist['rejected'][i]:>3}")

    out = os.path.join(HERE, "results", "eval_report.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("\nreport ->", out)


if __name__ == "__main__":
    main()
