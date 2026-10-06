"""演示：单轮 / 多轮预算内 / 预算耗尽 / 重复与相似切片 / 敏感度与噪声数据。

运行: python3 demo.py
输出: 控制台打印 + 生成 consumption_log.md（预算消耗记录）
"""

import random

from dp_agg import BudgetExhausted, DPAnalytics


def make_records(n=100_000, seed=2026):
    rng = random.Random(seed)
    regions = ["华东", "华北", "华南", "西南", "东北"]
    depts = ["销售", "研发", "运营", "客服"]
    channels = ["线上", "线下"]
    records = []
    for _ in range(n):
        records.append({
            "region": rng.choice(regions),
            "dept": rng.choice(depts),
            "channel": rng.choice(channels),
            "spend": round(rng.uniform(0, 500), 2),
        })
    return records


def fmt_filters(canon):
    if not canon:
        return "(全量)"
    return ",".join("%s=%s" % (k, v) for k, v in canon)


def ledger_lines(dp, title):
    lines = ["### %s" % title, "",
             "| 轮次 | 切片 | 聚合 | 本轮ε | 剩余ε | 敏感度Δf | 噪声尺度b | 复用 | 相似于 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for e in dp.ledger:
        sens = e.sensitivity
        scale = e.noise_scale
        if isinstance(sens, tuple):
            sens = "(%.0f, %.0f)" % sens
            scale = "(%.1f, %.1f)" % scale
        elif sens is None:
            sens, scale = "-", "-"
        else:
            sens = "%.1f" % sens
            scale = "%.2f" % scale
        similar = fmt_filters(e.similar_to) if e.similar_to else "-"
        lines.append("| %d | %s | %s%s | %.2f | %.2f | %s | %s | %s | %s |" % (
            e.round_id, fmt_filters(e.filters), e.agg,
            "(%s)" % e.field if e.field else "",
            e.epsilon_spent, e.epsilon_remaining, sens, scale,
            "是" if e.reused else "否", similar))
    lines.append("")
    return lines


def scenario_single(records, out):
    print("== 场景 1：单轮查询 ==")
    dp = DPAnalytics(records, epsilon_total=1.0,
                     contribution_bounds={"spend": 500.0}, seed=1)
    noisy = dp.query({"region": "华东"}, agg="count", epsilon=0.5)
    truth = dp.true_aggregate({"region": "华东"}, agg="count")
    print("count(华东) 真值=%.0f 含噪=%.2f 剩余预算=%.2f"
          % (truth, noisy, dp.accountant.remaining))
    out.extend(ledger_lines(dp, "场景 1：单轮查询（ε_total=1.0，本轮 ε=0.5）"))


def scenario_multi_round(records, out):
    print("\n== 场景 2：多轮查询（预算内，共享同一预算） ==")
    dp = DPAnalytics(records, epsilon_total=1.0,
                     contribution_bounds={"spend": 500.0}, seed=2)
    for region in ["华东", "华北", "华南", "西南", "东北"]:
        noisy = dp.query({"region": region}, agg="count", epsilon=0.2)
        print("count(%s) 含噪=%.2f 剩余预算=%.2f"
              % (region, noisy, dp.accountant.remaining))
    out.extend(ledger_lines(dp, "场景 2：多轮共享预算（ε_total=1.0，每轮 ε=0.2，累计 1.0）"))


def scenario_exhausted(records, out):
    print("\n== 场景 3：预算耗尽后明确拒绝 ==")
    dp = DPAnalytics(records, epsilon_total=0.3,
                     contribution_bounds={"spend": 500.0}, seed=3)
    for i, region in enumerate(["华东", "华北", "华南", "西南"], 1):
        try:
            noisy = dp.query({"region": region}, agg="count", epsilon=0.1)
            print("第%d轮 count(%s) 含噪=%.2f 剩余=%.2f"
                  % (i, region, noisy, dp.accountant.remaining))
        except BudgetExhausted as exc:
            print("第%d轮 count(%s) 被拒绝：%s（账本未增加消耗）"
                  % (i, region, exc))
    out.extend(ledger_lines(dp, "场景 3：预算耗尽（ε_total=0.3，第 4 轮被拒，账本仅 3 条）"))


def scenario_repeat_and_similar(records, out):
    print("\n== 场景 4：重复切片免费复用 + 相似切片识别 ==")
    dp = DPAnalytics(records, epsilon_total=1.0,
                     contribution_bounds={"spend": 500.0},
                     similarity_threshold=0.75, seed=4)
    q = {"region": "华东", "dept": "销售", "channel": "线上"}
    first = dp.query(q, agg="count", epsilon=0.3)
    print("首次查询  含噪=%.2f 消耗ε=0.30 剩余=%.2f"
          % (first, dp.accountant.remaining))
    for i in (2, 3):
        again = dp.query(q, agg="count", epsilon=0.3)
        e = dp.ledger[-1]
        print("第%d次重复 含噪=%.2f 消耗ε=%.2f（识别为重复，复用缓存）"
              % (i, again, e.epsilon_spent))
    similar_q = {"region": "华东", "dept": "销售", "channel": "线上",
                 "segment": "新客"}
    dp.query(similar_q, agg="count", epsilon=0.3)
    e = dp.ledger[-1]
    print("相似切片  Jaccard=3/4=0.75 ≥ 0.75 → 识别为相似于 %s，仍消耗ε=%.2f"
          % (fmt_filters(e.similar_to), e.epsilon_spent))
    out.extend(ledger_lines(
        dp, "场景 4：重复与相似切片（重复 0 消耗；相似被识别但照常扣预算）"))


def scenario_sensitivity_noise(records, out):
    print("\n== 场景 5：敏感度与噪声数据 ==")
    dp = DPAnalytics(records, epsilon_total=100.0,
                     contribution_bounds={"spend": 500.0}, seed=5)
    rows = []
    regions = ["华东", "华北", "华南", "西南", "东北", "华东"]
    cases = [
        ("count", None, 1.0), ("count", None, 0.5), ("count", None, 0.1),
        ("sum", "spend", 1.0), ("sum", "spend", 0.5), ("sum", "spend", 0.1),
    ]
    # 每档使用不同切片，避免命中重复缓存（重复复用见场景 4）
    slices = [{"region": r, "dept": d} for r, d in
              zip(regions, ["销售", "研发", "运营", "客服", "销售", "研发"])]
    for (agg, field, eps), flt in zip(cases, slices):
        dp.query(flt, agg=agg, field=field, epsilon=eps)
        e = dp.ledger[-1]
        rows.append((agg, field, eps, e.sensitivity, e.noise_scale))
    print("%-6s %-6s %-6s %-10s %-12s" % ("聚合", "字段", "ε", "敏感度Δf", "噪声尺度b=Δf/ε"))
    for agg, field, eps, sens, scale in rows:
        print("%-6s %-6s %-6.1f %-10.1f %-12.2f"
              % (agg, field or "-", eps, sens, scale))
    out.extend(["### 场景 5：敏感度与噪声（每档 ε 使用不同切片）", "",
                "| 聚合 | 字段 | ε | 敏感度 Δf | 噪声尺度 b=Δf/ε | 期望绝对误差 E|err|=b |",
                "|---|---|---|---|---|---|"])
    for agg, field, eps, sens, scale in rows:
        out.append("| %s | %s | %.1f | %.1f | %.2f | %.2f |"
                   % (agg, field or "-", eps, sens, scale, scale))
    out.append("")
    out.append("说明：count 的 Δf=1（增删一人计数至多变 1）；sum 的 Δf=截断上界 C=500"
               "（单人贡献被截断到 [0,500]）；Laplace 噪声的期望绝对误差等于尺度 b。")
    out.append("")


def main():
    records = make_records()
    out = ["# 预算消耗记录（demo.py 生成）", ""]
    scenario_single(records, out)
    scenario_multi_round(records, out)
    scenario_exhausted(records, out)
    scenario_repeat_and_similar(records, out)
    scenario_sensitivity_noise(records, out)
    with open("consumption_log.md", "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    print("\n消耗记录已写入 consumption_log.md")


if __name__ == "__main__":
    main()
