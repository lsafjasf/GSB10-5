"""演示：单轮、多轮共享预算、预算耗尽拒绝、重复/相似切片识别。

运行：python3 demo.py
产物：consumption_log.jsonl（每轮消耗记录）、sensitivity_noise_data.md
"""

import json
import random

from dpagg import BudgetLedger, BudgetExhaustedError, PrivateAggregator

random.seed(20261005)

DEPTS = ["engineering", "sales", "marketing", "hr"]
CITIES = ["shanghai", "beijing", "shenzhen"]
ROWS = [{"dept": random.choice(DEPTS), "city": random.choice(CITIES),
         "salary": random.uniform(5_000, 50_000)} for _ in range(10_000)]


def show(res):
    s = res["slice"]
    print(f"  切片状态={s['status']:<7} 指纹={s['fingerprint']} "
          f"相似度={s['similarity']:.2f} 缓存命中={res['cached']}")
    print(f"  本轮扣费 epsilon={res['charged']['epsilon']:.3f} "
          f"delta={res['charged']['delta']:.2g}")
    for n in res["noise"]:
        print(f"  噪声: 部分={n['part']} 敏感度={n['sensitivity']} "
              f"机制={n['mechanism']} 噪声参数={n['noise_param']:.4f} "
              f"epsilon={n['epsilon']}")
    ans = res["answer"]
    if isinstance(ans, float):
        print(f"  加噪答案={ans:.4f}")
    else:
        print(f"  加噪答案={ {k: round(v, 2) for k, v in ans.items()} }")


def main():
    print("=" * 72)
    print("场景 1：单轮查询（count，epsilon=0.1）")
    ledger = BudgetLedger(epsilon_total=1.0)
    agg = PrivateAggregator(ledger, seed=42)
    res = agg.ask("count", ROWS, {"dept": "engineering"}, epsilon=0.1)
    show(res)
    print(f"  账本: {ledger.summary()}")

    print("=" * 72)
    print("场景 2：多轮共享同一预算（sum/mean/histogram 混合）")
    res = agg.ask("sum", ROWS, {"dept": "engineering"}, epsilon=0.2,
                  value_field="salary", lo=0, hi=100_000)
    show(res)
    res = agg.ask("mean", ROWS, {"city": "shanghai"}, epsilon=0.3,
                  value_field="salary", lo=0, hi=100_000)
    show(res)
    res = agg.ask("histogram", ROWS, {"dept": "sales"}, epsilon=0.2,
                  value_field="city", buckets=CITIES)
    show(res)
    print(f"  账本: {ledger.summary()}")

    print("=" * 72)
    print("场景 3：重复查询同一切片（应 0 消耗并命中缓存）")
    res = agg.ask("count", ROWS, {"dept": "engineering"}, epsilon=0.1)
    show(res)
    print(f"  账本: {ledger.summary()}")

    print("=" * 72)
    print("场景 4：相似切片（子集谓词，应识别但仍全额扣费）")
    res = agg.ask("count", ROWS, {"dept": "engineering", "city": "shanghai"},
                  epsilon=0.1)
    show(res)
    print(f"  账本: {ledger.summary()}")

    print("=" * 72)
    print("场景 5：预算耗尽后明确拒绝")
    try:
        agg.ask("count", ROWS, {"dept": "hr"}, epsilon=0.5)
    except BudgetExhaustedError as exc:
        print(f"  已拒绝: {exc}")
    print(f"  账本: {ledger.summary()}（拒绝不扣费）")

    ledger.export_json("consumption_log.jsonl")
    print("\n消耗记录已写入 consumption_log.jsonl")

    print("=" * 72)
    print("敏感度与噪声标定数据（Laplace b=敏感度/epsilon；"
          "Gaussian sigma=敏感度*sqrt(2ln(1.25/delta))/epsilon, delta=1e-5）")
    import math
    rows = [("count", 1.0), ("sum(lo=0,hi=100000)", 100_000.0),
            ("histogram", 1.0), ("mean.sum", 100_000.0), ("mean.count", 1.0)]
    lines = ["| 查询 | 敏感度 | epsilon | Laplace b | Gaussian sigma |",
             "|---|---|---|---|---|"]
    for name, s in rows:
        for eps in (0.05, 0.1, 0.5):
            b = s / eps
            sigma = s * math.sqrt(2 * math.log(1.25 / 1e-5)) / eps
            lines.append(f"| {name} | {s:g} | {eps} | {b:,.2f} | {sigma:,.2f} |")
            print(f"  {name:<22} 敏感度={s:<10g} eps={eps:<5} "
                  f"Laplace b={b:>14,.2f}  Gaussian sigma={sigma:>14,.2f}")
    with open("sensitivity_noise_data.md", "w", encoding="utf-8") as fh:
        fh.write("# 敏感度与噪声标定数据\n\n"
                 "Laplace: b = 敏感度 / epsilon；"
                 "Gaussian: sigma = 敏感度 * sqrt(2 ln(1.25/delta)) / epsilon"
                 "（delta=1e-5）\n\n" + "\n".join(lines) + "\n")
    print("\n标定数据已写入 sensitivity_noise_data.md")


if __name__ == "__main__":
    main()
