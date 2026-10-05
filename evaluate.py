"""evaluate -- 召回率 / 误报率评测。

测试集构造（固定随机种子，可复现）：
  受保护库 3150 篇：
    2500 篇随机正文（15~35 句，约 250~600 字）
     500 篇在头尾加入共享套话 BOILER（构造模板型误报来源）
     150 篇短文档（约 25~70 字）
  正样本（泄露/改写，共 780 条）：
    完全相同 80，改字 1%/3%/5% 各 80，句子换序 80，
    拆散拼接（50字片段/100字片段）各 80，
    短文档照抄 60，短文档改 1~2 字 60
  负样本（共 550 条）：
    完全无关随机文档 200，无关短文档(20~60字) 200，
    仅含共享套话+少量正文 150
判定：query 返回任一文档即视为命中。默认参数 threshold=0.10、
min_matches=4、max_df=32。
运行：python3 evaluate.py
"""

import random
import time

from leakfp import LeakDetector
from textgen import BOILER, corrupt, gen_doc, gen_short_doc, reorder, splice

THRESHOLDS = [0.05, 0.10, 0.15, 0.20, 0.30, 0.40]
MIN_MATCHES = 4
MAX_DF = 32


def build_dataset(rng: random.Random):
    protected = [gen_doc(rng, rng.randint(15, 35)) for _ in range(2500)]
    for _ in range(500):
        protected.append(
            rng.choice(BOILER) + "。" + gen_doc(rng, rng.randint(10, 25))
            + "。" + rng.choice(BOILER) + "。"
        )
    short = [gen_short_doc(rng) for _ in range(150)]

    pos = {}
    idx = lambda: rng.randrange(len(protected))
    pos["完全相同"] = [protected[idx()] for _ in range(80)]
    pos["改字1%"] = [corrupt(protected[idx()], rng, 0.01) for _ in range(80)]
    pos["改字3%"] = [corrupt(protected[idx()], rng, 0.03) for _ in range(80)]
    pos["改字5%"] = [corrupt(protected[idx()], rng, 0.05) for _ in range(80)]
    pos["句子换序"] = [reorder(protected[idx()], rng) for _ in range(80)]
    pos["拆散拼接50字"] = [splice(protected[idx()], rng, 50) for _ in range(80)]
    pos["拆散拼接100字"] = [splice(protected[idx()], rng, 100) for _ in range(80)]
    pos["短文档照抄"] = [short[rng.randrange(len(short))] for _ in range(60)]
    pos["短文档改1-2字"] = [corrupt(short[rng.randrange(len(short))], rng, 0.03)
                           for _ in range(60)]

    neg = {
        "完全无关": [gen_doc(rng, rng.randint(15, 35)) for _ in range(200)],
        "无关短文本": [gen_short_doc(rng) for _ in range(200)],
        "套话型负样本": [
            rng.choice(BOILER) + "。" + gen_doc(rng, rng.randint(1, 2))
            + "。" + rng.choice(BOILER) + "。"
            for _ in range(150)
        ],
    }
    return protected, short, pos, neg


def best_score(det: LeakDetector, text: str, max_df):
    """取最高分及命中指纹数（threshold=0、min_matches=1，阈值离线施加）。"""
    res = det.query(text, threshold=0.0, min_matches=1, max_df=max_df, top=1)
    return (res[0][1], res[0][2]) if res else (0.0, 0)


def pct(n, d):
    return f"{100.0 * n / d:5.1f}%"


def main() -> None:
    rng = random.Random(20261005)
    protected, short, pos, neg = build_dataset(rng)

    det = LeakDetector()
    t0 = time.perf_counter()
    det.add_many(protected + short)
    det.finalize()
    build_s = time.perf_counter() - t0
    st = det.stats()
    print(f"建库 {st['docs']} 篇 / {st['fingerprints']} 个指纹 / "
          f"索引 {st['index_bytes'] / 1e6:.1f} MB / 耗时 {build_s:.2f}s\n")

    # 用默认 max_df 跑全部查询，保存 (score, matches)
    pos_scores = {name: [best_score(det, t, MAX_DF) for t in texts]
                  for name, texts in pos.items()}
    neg_scores = {name: [best_score(det, t, MAX_DF) for t in texts]
                  for name, texts in neg.items()}

    # 套话负样本额外用 max_df=None 跑一遍，展示 max_df 的作用
    boiler_off = [best_score(det, t, None) for t in neg["套话型负样本"]]

    def hit_rate(scores, th):
        hits = sum(1 for s, m in scores if s >= th and m >= MIN_MATCHES)
        return hits, len(scores)

    print("== 阈值扫描（min_matches=%d，max_df=%s）==" % (MIN_MATCHES, MAX_DF))
    header = f"{'类别':<16}" + "".join(f"{'t=' + str(t):>9}" for t in THRESHOLDS)
    print(header)
    groups = [(n, pos_scores[n], True) for n in pos_scores] + \
             [(n, neg_scores[n], False) for n in neg_scores]
    for name, scores, is_pos in groups:
        cells = []
        for th in THRESHOLDS:
            hits, total = hit_rate(scores, th)
            cells.append(f"{100.0 * hits / total:8.1f}%")
        print(f"{name:<16}" + "".join(cells))

    # 总体召回/误报（默认阈值 0.10）
    th = 0.10
    all_pos = [s for scores in pos_scores.values() for s in scores]
    all_neg = [s for scores in neg_scores.values() for s in scores]
    ph, pt = hit_rate(all_pos, th)
    nh, nt = hit_rate(all_neg, th)
    print(f"\n== 默认参数 threshold={th} min_matches={MIN_MATCHES} "
          f"max_df={MAX_DF} ==")
    print(f"总召回率 {pct(ph, pt)}  ({ph}/{pt})")
    print(f"总误报率 {pct(nh, nt)}  ({nh}/{nt})")

    # 平均最高重合度（观察分数分布间隔）
    print("\n== 各类别平均最高重合度 / 平均命中指纹数 ==")
    for name, scores, _ in groups:
        avg_s = sum(s for s, _ in scores) / len(scores)
        avg_m = sum(m for _, m in scores) / len(scores)
        print(f"{name:<16} score={avg_s:.3f}  matches={avg_m:.1f}")

    # 短文档专项：按长度分桶看负样本误报与正样本召回
    print("\n== 短文档专项（threshold=0.10, min_matches=4）==")
    print(f"{'长度区间':<12}{'负样本误报':<14}{'短正样本召回':<14}")
    neg_short = list(zip(neg["无关短文本"], neg_scores["无关短文本"]))
    pos_short = list(zip(
        pos["短文档照抄"] + pos["短文档改1-2字"],
        pos_scores["短文档照抄"] + pos_scores["短文档改1-2字"]))
    for lo, hi in [(0, 25), (25, 35), (35, 50), (50, 80)]:
        nb = [p for t, p in neg_short if lo <= len(t) < hi]
        pb = [p for t, p in pos_short if lo <= len(t) < hi]
        nf = f"{pct(sum(1 for s,m in nb if s>=th and m>=4), len(nb))} ({len(nb)})" if nb else "-"
        pr = f"{pct(sum(1 for s,m in pb if s>=th and m>=4), len(pb))} ({len(pb)})" if pb else "-"
        print(f"{str((lo,hi)):<12}{nf:<14}{pr:<14}")

    # 套话专项：max_df 开关对比
    h_on = sum(1 for s, m in neg_scores["套话型负样本"]
               if s >= th and m >= MIN_MATCHES)
    h_off = sum(1 for s, m in boiler_off if s >= th and m >= MIN_MATCHES)
    print(f"\n== 套话型负样本误报控制（{len(boiler_off)} 条，threshold={th}）==")
    print(f"max_df=32（开启）  误报 {pct(h_on, len(boiler_off))}")
    print(f"max_df=None（关闭）误报 {pct(h_off, len(boiler_off))}")


if __name__ == "__main__":
    main()
