#!/usr/bin/env python3
"""End-to-end demonstration: identify -> plan -> guard -> merge -> measure.

Run:
    python3 demo.py

Prints the identification evidence tables, per-rule result tables,
counterexample verification and pre/post cardinality comparison in Chinese.
"""

from __future__ import annotations

from cardinality_guard import (
    CardinalityDetector, auto_plan, validate_policy, measure_merged,
    Policy, PolicyError, Rule, OTHER,
)
from cardinality_guard.data import generate

LINE = "=" * 78


def pct(x: float) -> str:
    return f"{x:.3f}"


def section(title: str) -> None:
    print(f"\n{LINE}\n{title}\n{LINE}")


def detect(scenario: str, n: int, n_windows: int = 8):
    det = CardinalityDetector()
    stream = generate(n, scenario=scenario, n_windows=n_windows)
    for window, labels in stream:
        det.feed(labels, window)
    return det, det.report()


def print_detection_table(rep, limit: int | None = None) -> None:
    rows = list(rep.labels.values())
    if limit:
        rows = rows[:limit]
    print(f"{'标签':<10}{'样本量':>10}{'取值数量':>12}{'选择率':>9}"
          f"{'归一化熵':>10}{'增长率':>9}  判定")
    for r in rows:
        verdict = "高基数" if r.is_high_cardinality else "低基数"
        print(f"{r.name:<10}{r.total:>10,}{r.distinct:>12,.0f}{r.selectivity:>9.3f}"
              f"{r.entropy_norm:>10.3f}{r.growth_rate:>9.3f}  {verdict}")
        for reason in r.reasons:
            print(f"{'':<12}└─ {reason}")


def print_reasons_explainer() -> None:
    section("一、识别依据（三维 + 组合风险）")
    print("""\
1. 取值数量 distinct：每标签独立观测；未超 exact_limit=256 时为精确集合，
   超过后提升为 HyperLogLog(p=12，误差约1.6%)，内存恒为 4KB/草图。
   阈值 high_value_count = 1000。
2. 增长速率 growth_rate：最近 3 个窗口内“本窗口新出现取值数 / 本窗口序列数”
   的均值。有限维度（region/status）长期趋于 0；无界 id 每个窗口都接近 1。
   阈值 high_growth_rate = 0.25，且需连续 >=3 个窗口（持续增长）。
3. 区分度 distinctiveness：
   - 选择率 selectivity = distinct / total，接近 1 表示取值几乎不重复（id）；
   - 归一化熵 entropy_norm（0..1，基于 512 点蓄水池采样），接近 1 表示
     取值均匀铺开；又均匀又唯一 => 该标签没有可聚合的“热值”。
   阈值 high_selectivity = 0.5。
满足任意一条即判为高基数；三条全部满足是“随机 id”的强信号。
4. 组合风险：标签对的联合草图按 1/4 抽稀子流更新（提速），在子流内计算
   实现率 = 联合取值数 / min(子流样本量, 边际基数乘积)，>= 0.8 且边际乘积
   >= 4096 判为“组合完全展开”。""")


def print_scenario_summary() -> None:
    section("二、四类情形的识别结果（各 20,000 条）")
    for sc, desc in (("low", "低基数"),
                     ("single", "单个高基数标签"),
                     ("combo", "多标签组合爆炸"),
                     ("growth", "取值持续增长")):
        print(f"\n--- 情形: {desc} ({sc}) ---")
        _, rep = detect(sc, 20_000)
        print_detection_table(rep)
        hc = rep.high_cardinality_labels
        print(f"=> 高基数标签: {hc if hc else '无'}")
        if rep.exploding_pairs:
            top3 = ", ".join(
                f"{a}×{b}(实现率={p['factor']:.2f})"
                for p in rep.exploding_pairs[:3] for a, b in [p["pair"]])
            print(f"=> 组合完全展开的标签对: {top3}")


def collect_examples(n: int):
    """Pick representative raw values for each high-cardinality label."""
    examples = {}
    for _, labels in generate(n, scenario="combo"):
        for k in ("trace_id", "order_id", "path", "pod"):
            examples.setdefault(k, []).append(labels[k])
    out = {}
    for k, vals in examples.items():
        uniq = []
        for v in vals:
            if v not in uniq:
                uniq.append(v)
            if len(uniq) >= 3:
                break
        out[k] = uniq
    return out


def print_rule_tables(policy, examples) -> None:
    section("四、三种归并规则的结果表")
    from cardinality_guard.policy import merge_labels

    print("规则 1：DROP（丢弃）——取值无查询语义的随机 id")
    print(f"{'原始取值':<48} {'归并结果':<10}")
    for v in examples["trace_id"]:
        m = merge_labels({"trace_id": v}, policy)
        print(f"{v:<48} {m.get('trace_id', '<已丢弃>'):<10}")

    print("\n规则 2：NORMALIZE（归一化）——按动态形状收敛，保留结构语义")
    print(f"{'标签':<9} {'原始取值':<32} {'归并结果':<24}")
    for k in ("order_id", "path"):
        for v in examples[k]:
            m = merge_labels({k: v}, policy)
            print(f"{k:<9} {v:<32} {m.get(k, '<已丢弃>'):<24}")

    print("\n规则 3：TRUNCATE（截断）——保留 top-K 热值 + pinned 值，其余归 OTHER")
    print(f"{'原始取值':<14} {'归并结果':<14} 说明")
    hot = {v for v, _ in DET_SKETCHES["pod"].top_values(8)}
    top_values = {"pod": hot | {"pod-99999"}}
    shown_hot = sorted(hot)[:2] + ["pod-09999", "pod-18765", "pod-99999"]
    for v in shown_hot:
        m = merge_labels({"pod": v}, policy, top_values)
        note = "热值保留" if v in hot else ("pinned 保留" if v == "pod-99999"
                                            else "长尾 -> " + OTHER)
        print(f"{v:<14} {m['pod']:<14} {note}")

    print(f"\n归并常量：其他值 -> {OTHER!r}（查询时仍可整体过滤）")


def print_counterexamples() -> None:
    section("五、反例验证：归并不得合并本应区分的维度")

    def check(name, policy, **kw):
        try:
            validate_policy(policy, **kw)
            print(f"  [{name}] 通过：本应区分的序列归并后仍然不同")
        except PolicyError as e:
            print(f"  [{name}] 拒绝（PolicyError）：{e}")

    print("反例1：两条仅 request_id 不同的序列，drop 该标签会使其合并 => 必须拒绝")
    check("drop 会合并差异", Policy(
        rules={"request_id": Rule("drop", force=True)},
        must_distinguish=(({"svc": "a", "request_id": "r1"},
                           {"svc": "a", "request_id": "r2"}),)))

    print("反例2：tenant 是受保护计费维度，配置 truncate => 必须拒绝")
    check("受保护标签", Policy(
        rules={"tenant": Rule("truncate", k=4)},
        protected_labels=frozenset({"tenant"})))

    print("反例3：对低选择率的真维度 status 执行 drop => 必须拒绝")
    check("低选择率维度禁 drop", Policy(
        rules={"status": Rule("drop")}),
        report=LOW_REP)

    print("反例4：归一化必须让静态值穿透（200/500 不得合并）")
    from cardinality_guard.policy import merge_labels
    p = Policy(rules={"status": Rule("normalize")})
    a, b = merge_labels({"status": "200"}, p), merge_labels({"status": "500"}, p)
    ok = a != b
    print(f"  200 -> {a['status']}, 500 -> {b['status']}；"
          f"保持区分: {'是' if ok else '否（失败！）'}")
    assert ok

    print("反例5：差异不在被 drop 标签上（status 200 vs 500），drop trace_id 安全")
    check("drop 不影响差异", Policy(
        rules={"trace_id": Rule("drop", force=True)},
        must_distinguish=(({"status": "200", "trace_id": "x" * 32},
                           {"status": "500", "trace_id": "y" * 32}),)))


def print_cardinality_compare(n: int, rep, policy, top_values) -> None:
    section("六、归并前后基数变化（combo 情形，精确统计）")
    merged_n, merged_labels = measure_merged(
        (labels for _, labels in generate(n, scenario="combo")),
        policy, top_values, exact_limit=n + 1)
    raw_n = n  # 每条序列都带唯一 trace/order，原始序列即互不相同
    print(f"原始时间序列基数: {raw_n:,}")
    print(f"归并后时间序列基数: {merged_n:,}  "
          f"（下降 {(1 - merged_n / raw_n) * 100:.1f}%）\n")
    print(f"{'标签':<10} {'归并前取值数':>14} {'归并后取值数':>14} {'规则':<10}")
    for name, lr in rep.labels.items():
        rule = policy.rules.get(name)
        action = rule.action if rule else "keep"
        after = merged_labels.get(name, lr.distinct)
        if action == "drop":
            after_disp = "0（删除）"
        else:
            after_disp = f"{after:,.0f}"
        print(f"{name:<10} {lr.distinct:>14,.0f} {after_disp:>14} {action:<10}")

    section("七、对查询表达力的影响")
    print("""\
| 规则       | 仍可表达的查询                              | 不再可表达的查询            |
|------------|---------------------------------------------+-----------------------------|
| DROP       | （无，该标签不可再过滤/分组）               | 该标签全部查询（仅限随机id）|
| NORMALIZE  | 按渠道(ORD-EU)/路径模板(/api/users/{id})分组 | 按单个 order/trace 精确过滤 |
| TRUNCATE   | 按 top-K 热值、pinned 值、__OTHER__ 分组     | 长尾各取值互相区分          |

表达力损失是显式且可控的：被 drop 的只有选择率>=0.9 且熵>=0.9 的随机 id；
NORMALIZE 保留结构维度（渠道/路径模板）；TRUNCATE 可用 pinned 强制保留
任何业务要求逐值区分的取值（如 status=500）。受保护标签（tenant 计费维度）
被自动 keep，规则配置阶段直接拒绝。""")


DET_SKETCHES = {}
LOW_REP = None


def main() -> None:
    global DET_SKETCHES, LOW_REP
    n = 120_000
    print_reasons_explainer()
    print_scenario_summary()
    _, LOW_REP = detect("low", 5_000)

    section("三、自动归并方案（combo 情形，n=120,000）")
    det, rep = detect("combo", n)
    DET_SKETCHES = det.sketches
    plan = auto_plan(rep, sketches=det.sketches)
    print(f"{'标签':<10} {'规则':<10} {'归并前':>12} {'估计归并后':>12}  依据")
    rules = {}
    for e in plan:
        print(f"{e.label:<10} {e.rule.action:<10} {e.distinct_before:>12,.0f}"
              f" {e.est_distinct_after:>12,.0f}  {e.reason}")
        rules[e.label] = e.rule

    # 受保护维度演示：tenant 高基数但禁止归并
    d2 = CardinalityDetector()
    for i in range(20_000):
        d2.feed({"tenant": f"tenant-{i % 5000:04d}",
                 "order_id": f"ORD-{i:08d}"}, window=i // 2500)
    r2 = d2.report()
    p2 = auto_plan(r2, sketches=d2.sketches, protected_labels={"tenant"})
    for e in p2:
        tag = "（受保护）" if e.rule.action == "keep" else ""
        print(f"{e.label:<10} {e.rule.action:<10} {e.distinct_before:>12,.0f}"
              f" {e.est_distinct_after:>12,.0f}  {e.reason}{tag}")

    policy = Policy(rules=rules, protected_labels=frozenset({"tenant"}))
    validate_policy(policy, report=rep)
    top_values = {
        name: {v for v, _ in det.sketches[name].top_values(rule.k)}
        for name, rule in rules.items() if rule.action == "truncate"
    }

    print_rule_tables(policy, collect_examples(n))
    print_counterexamples()
    print_cardinality_compare(n, rep, policy, top_values)


if __name__ == "__main__":
    main()
