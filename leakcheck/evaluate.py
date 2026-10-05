"""召回率 / 误报率评测。

测试集构造（固定随机种子，可复现）
----------------------------------
- 词表：1200 个英文伪词 + 800 个常用汉字，文档由词表随机采样生成；
  另设 16 条固定"套话"短句（免责声明、版权声明一类），30% 的基线文档
  会夹带 1~3 条套话，用来模拟真实语料里的公共片段。
- 基线库：500 篇，长度 150~400 token。
- 正例（每组 200 篇，每篇对应一个已知源文档）：
    exact   完全相同
    edit    随机替换 5% 的 token（模拟少量改字）
    reorder 把文档切成 5 段打乱顺序（模拟换序）
    splice  从源文档抽 2 段 40~70 token 的碎片，分别嵌进全新随机文本
- 负例：
    unrelated   500 篇全新随机文档
    boilerplate 300 篇全新文档，夹带 1~2 条与库中相同的套话
    short       300 篇 14~60 token 的短文档（其中一半含一条套话）

判定：query 命中任意源文档且不同指纹命中数 >= min_hits(默认6) 即算报警。
另统计 top1 是否恰好是真正的源文档（定位准确率）。
"""
import random

from leakfp import LeakIndex, tokenize

SEED = 20261005
ENGLISH = [f"word{i}" for i in range(1200)]
HAN = list("的一是不了人我在有他这为之大来以个中上们到说时地也子就道会要那下看天时年")
HAN = list(set(HAN))[:40]  # 用少量常用字提高自然碰撞概率，更接近真实
VOCAB = ENGLISH + HAN

BOILERPLATES = [
    "本页面内容仅供参考，不构成任何投资建议",
    "转载请注明出处并保留全部版权声明",
    "最终解释权归本公司所有，如有变更恕不另行通知",
    "投资有风险，入市需谨慎，过往业绩不代表未来表现",
]


def make_doc(rng, lo=150, hi=400, boiler_prob=0.0):
    n = rng.randint(lo, hi)
    toks = [rng.choice(VOCAB) for _ in range(n)]
    text = " ".join(toks)
    extras = []
    if rng.random() < boiler_prob:
        for bp in rng.sample(BOILERPLATES, rng.randint(1, 2)):
            extras.append(bp)
    return " ".join(extras + [text]) if extras else text


def variant_exact(rng, base):
    return base


def variant_edit(rng, base, rate=0.05):
    toks = tokenize(base)
    out = []
    for t in toks:
        if rng.random() < rate:
            out.append(rng.choice(VOCAB))
        else:
            out.append(t)
    return " ".join(out)


def variant_reorder(rng, base, blocks=5):
    toks = tokenize(base)
    size = max(1, len(toks) // blocks)
    chunks = [toks[i : i + size] for i in range(0, len(toks), size)]
    rng.shuffle(chunks)
    return " ".join(t for c in chunks for t in c)


def variant_splice(rng, base):
    toks = tokenize(base)
    a, b = rng.sample(range(0, len(toks) - 70), 2)
    frags = [" ".join(toks[a : a + rng.randint(40, 70)]),
             " ".join(toks[b : b + rng.randint(40, 70)])]
    noise = " ".join(rng.choice(VOCAB) for _ in range(400))
    return f"{noise[:500]} {frags[0]} {noise[500:1000]} {frags[1]} {noise[1000:]}"


def run(n_per_group=200):
    rng = random.Random(SEED)
    idx = LeakIndex()

    n_base = 500
    bases = []
    for i in range(n_base):
        doc = make_doc(rng, boiler_prob=0.3)
        bases.append(doc)
        idx.add(f"base-{i}", doc)

    groups = {
        "exact   (完全相同)": (variant_exact, 200, True),
        "edit    (改字 5%)  ": (variant_edit, n_per_group, True),
        "reorder (段落换序) ": (variant_reorder, n_per_group, True),
        "splice  (拆散拼接) ": (variant_splice, n_per_group, True),
        "unrelated (无关长文)": (lambda r, b: make_doc(r), 500, False),
        "boilerplate(仅套话) ": (lambda r, b: make_doc(r, boiler_prob=1.0), 300, False),
        "short   (短文档)    ": (
            lambda r, b: (
                ((r.choice(BOILERPLATES) + " ") if r.random() < 0.5 else "")
                + " ".join(r.choice(VOCAB) for _ in range(r.randint(14, 60)))
            ),
            300,
            False,
        ),
    }

    print(f"库规模: {idx.stats()}\n")
    print(f"{'组别':<22}{'样本':>5}{'报警':>6}{'召回/误报率':>12}{'top1正确':>10}")
    print("-" * 58)

    tp_total = fp_total = pos_total = neg_total = top1_ok = flagged_total = 0
    for name, (fn, count, is_pos) in groups.items():
        flagged = top1 = 0
        for _ in range(count):
            src = f"base-{rng.randrange(n_base)}"
            text = fn(rng, bases[int(src.split('-')[1])])
            hits = idx.query(text)
            if hits:
                flagged += 1
                if hits[0]["doc_id"] == src:
                    top1 += 1
        rate = flagged / count
        print(f"{name:<22}{count:>5}{flagged:>6}{rate*100:>11.1f}%{top1:>10}")
        flagged_total += flagged
        if is_pos:
            tp_total += flagged
            pos_total += count
            top1_ok += top1
        else:
            fp_total += flagged
            neg_total += count

    print("-" * 58)
    print(f"正例总体召回率: {tp_total}/{pos_total} = {tp_total/pos_total*100:.2f}%")
    print(f"负例总体误报率: {fp_total}/{neg_total} = {fp_total/neg_total*100:.2f}%")
    print(f"报警时 top1 源文档正确: {top1_ok}/{flagged_total - fp_total}"
          f" = {top1_ok/max(1, flagged_total-fp_total)*100:.2f}%")


if __name__ == "__main__":
    run()
