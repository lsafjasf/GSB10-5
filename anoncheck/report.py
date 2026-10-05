"""检查流程编排：识别 QI -> 枚举组合 -> 量化风险 -> 输出清单 ->
泛化/抑制 -> 处理前后对照，最终生成 Markdown 报告与 CSV 清单。
"""

import csv
import os

from .quasi import identify_quasi_identifiers, enumerate_combinations
from .risk import evaluate_combination, find_risky_records
from .transform import (
    age_band, zip_prefix, category_mapper,
    apply_generalization, suppress_risky_records,
)
from .dataset import OCCUPATION_CATEGORY


def _combo_label(names):
    return " + ".join(names)


def _default_generalization_plan(headers, qi_columns):
    """按列名给出泛化方案，返回 {列下标: (方案描述, 泛化函数)}。"""
    plan = {}
    for qi in qi_columns:
        name = qi["column"].lower()
        idx = qi["index"]
        if "age" in name or "年龄" in name:
            plan[idx] = ("年龄 -> 10 岁区间（如 34 -> 30-39）", age_band(10))
        elif "zip" in name or "postal" in name or "邮编" in name:
            plan[idx] = ("邮编 -> 保留前 3 位（如 100084 -> 100***）",
                         zip_prefix(3))
        elif "occupation" in name or "job" in name or "职业" in name:
            plan[idx] = ("职业 -> 职业大类（如 nurse -> professional）",
                         category_mapper(OCCUPATION_CATEGORY))
    return plan


def run_check(headers, rows, out_dir, k_threshold=5, max_order=3,
              qi_sample=50000, list_top=3, list_preview=10):
    """执行完整检查并把结果写入 out_dir，返回报告文本。"""
    os.makedirs(out_dir, exist_ok=True)
    id_index = headers.index("record_id") if "record_id" in headers else None

    # 1. 准标识符识别（大样本上抽样判定，结论与全量一致）
    sample = rows[:qi_sample]
    qis = identify_quasi_identifiers(headers, sample)

    # 2. 枚举组合并逐组评估
    combos = enumerate_combinations(qis, max_order=max_order)
    evaluations = []
    for names, indices in combos:
        metrics = evaluate_combination(rows, indices, k_threshold)
        evaluations.append({"names": names, "indices": indices,
                            "metrics": metrics})

    # 3. 选中高风险组合：存在稀有记录的组合，按稀有记录数降序
    selected = [e for e in evaluations
                if e["metrics"]["rare_record_count"] > 0]
    selected.sort(key=lambda e: (-e["metrics"]["rare_record_count"],
                                 e["names"]))

    # 4. 高风险记录清单（可复核 CSV）
    list_files = []
    previews = {}
    for rank, entry in enumerate(selected[:list_top], start=1):
        records = find_risky_records(rows, entry["indices"],
                                     k_threshold, id_index)
        fname = f"high_risk_{rank}_{'_'.join(entry['names'])}.csv"
        path = os.path.join(out_dir, fname)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["record_id", "row_number", "class_size"]
                            + list(entry["names"]))
            for rec in records:
                writer.writerow([rec["record_id"], rec["row_number"],
                                 rec["class_size"], *rec["key"]])
        list_files.append((entry["names"], fname, len(records)))
        previews[entry["names"]] = records[:list_preview]

    # 5. 泛化：对全部 QI 应用默认方案，重估所有组合
    plan = _default_generalization_plan(headers, qis)
    gen_rows = apply_generalization(
        rows, {idx: func for idx, (desc, func) in plan.items()})
    after_gen = []
    for names, indices in combos:
        metrics = evaluate_combination(gen_rows, indices, k_threshold)
        after_gen.append({"names": names, "indices": indices,
                          "metrics": metrics})

    # 6. 抑制：针对泛化后风险最高的组合，抑制仍稀有的记录
    worst = max(after_gen, key=lambda e: e["metrics"]["rare_record_count"])
    sup_rows, suppressed = suppress_risky_records(
        gen_rows, worst["indices"], k_threshold)
    after_sup = evaluate_combination(sup_rows, worst["indices"], k_threshold)

    report = _render_report(
        headers=headers, n_rows=len(rows), qis=qis,
        k_threshold=k_threshold, max_order=max_order,
        evaluations=evaluations, selected=selected,
        list_files=list_files, previews=previews,
        plan=plan, after_gen=after_gen,
        worst=worst, suppressed=suppressed, after_sup=after_sup,
        out_dir=out_dir)
    with open(os.path.join(out_dir, "report.md"), "w",
              encoding="utf-8") as fh:
        fh.write(report)
    return report


def _fmt_pct(x):
    return f"{x:.2%}"


def _render_report(headers, n_rows, qis, k_threshold, max_order,
                   evaluations, selected, list_files, previews,
                   plan, after_gen, worst, suppressed, after_sup,
                   out_dir):
    lines = []
    add = lines.append
    add("# 匿名化可逆性检查报告")
    add("")
    add(f"- 记录数：{n_rows}")
    add(f"- 稀有组合阈值 k = {k_threshold}（等价类大小 < k 视为高风险）")
    add(f"- 组合阶数：1..{max_order}")
    add("")

    add("## 1. 准标识符识别")
    add("")
    add("识别规则：字段名命中 QI 词表，或统计特征处于可组合区间"
        "（非常量、非近唯一、基数 >= 2）。直接标识符与敏感属性剔除。")
    add("")
    add("| 字段 | 类别 | 基数 | 基数占比 | 理由 |")
    add("| --- | --- | --- | --- | --- |")
    for qi in qis:
        add(f"| {qi['column']} | {qi['category']} | {qi['distinct']} "
            f"| {_fmt_pct(qi['ratio'])} | {qi['reason']} |")
    add("")
    add(f"全部字段：{', '.join(headers)}")
    add("")

    add("## 2. 各组合风险量化")
    add("")
    add("| 组合 | 等价类数 | 最小类 | 平均类 | 最大类 | 唯一组合数 "
        "| 稀有组合数 | 稀有记录数 | 稀有记录占比 | 检察官风险 |")
    add("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for e in evaluations:
        m = e["metrics"]
        add(f"| {_combo_label(e['names'])} | {m['class_count']} "
            f"| {m['min_class_size']} | {m['mean_class_size']:.1f} "
            f"| {m['max_class_size']} | {m['unique_class_count']} "
            f"| {m['rare_class_count']} | {m['rare_record_count']} "
            f"| {_fmt_pct(m['rare_record_ratio'])} "
            f"| {m['prosecutor_risk']:.4f} |")
    add("")

    add("## 3. 被选中的高风险组合")
    add("")
    add("筛选规则：稀有记录数 > 0 的组合，按稀有记录数降序。")
    add("")
    if selected:
        for i, e in enumerate(selected, start=1):
            m = e["metrics"]
            add(f"{i}. `{_combo_label(e['names'])}`：稀有组合 "
                f"{m['rare_class_count']} 个，涉及 {m['rare_record_count']} "
                f"条记录（最小等价类 {m['min_class_size']}）")
    else:
        add("（无）所有组合均满足 k-匿名。")
    add("")

    add("## 4. 高风险记录清单")
    add("")
    if list_files:
        add("完整清单（记录级、可复核）已写入：")
        add("")
        for names, fname, count in list_files:
            add(f"- `{fname}`：组合 `{_combo_label(names)}`，"
                f"{count} 条记录")
        add("")
        add("各清单前若干行预览：")
        add("")
        for names, records in previews.items():
            add(f"组合 `{_combo_label(names)}`：")
            add("")
            add("| record_id | row | 类大小 | 取值 |")
            add("| --- | --- | --- | --- |")
            for rec in records:
                add(f"| {rec['record_id']} | {rec['row_number']} "
                    f"| {rec['class_size']} | {', '.join(map(str, rec['key']))} |")
            add("")
    else:
        add("（无高风险记录）")
        add("")

    add("## 5. 处理前后对照")
    add("")
    add("### 5.1 泛化方案")
    add("")
    if plan:
        for idx in sorted(plan):
            add(f"- 列 `{headers[idx]}`：{plan[idx][0]}")
    else:
        add("（无可用泛化方案）")
    add("")
    add("### 5.2 泛化前后风险对照")
    add("")
    add("| 组合 | 最小类 前->后 | 唯一组合 前->后 | 稀有组合 前->后 "
        "| 稀有记录 前->后 |")
    add("| --- | --- | --- | --- | --- |")
    gen_by_names = {e["names"]: e["metrics"] for e in after_gen}
    for e in evaluations:
        before = e["metrics"]
        after = gen_by_names[e["names"]]
        add(f"| {_combo_label(e['names'])} "
            f"| {before['min_class_size']} -> {after['min_class_size']} "
            f"| {before['unique_class_count']} -> {after['unique_class_count']} "
            f"| {before['rare_class_count']} -> {after['rare_class_count']} "
            f"| {before['rare_record_count']} -> {after['rare_record_count']} |")
    add("")
    add("### 5.3 抑制")
    add("")
    wm = worst["metrics"]
    add(f"泛化后风险最高的组合为 `{_combo_label(worst['names'])}`"
        f"（稀有记录 {wm['rare_record_count']} 条）。"
        f"对其中仍稀有的记录执行抑制（组合列置为 `*`），"
        f"共抑制 {suppressed} 条。")
    add("")
    add(f"抑制后该组合：最小等价类 {after_sup['min_class_size']}，"
        f"唯一组合 {after_sup['unique_class_count']} 个，"
        f"稀有组合 {after_sup['rare_class_count']} 个，"
        f"稀有记录 {after_sup['rare_record_count']} 条。")
    add("")
    add(f"（清单与报告输出目录：`{out_dir}`）")
    add("")
    return "\n".join(lines)
