# -*- coding: utf-8 -*-
"""示例数据集：支付/搜索/订单/基础设施四个命名空间。

运行后输出：
  1. 各层影响清单（直接引用 / 间接受影响 / 待人工确认）与推导路径
  2. 同名不同义样例的判定结果
  3. 与人工标注真值相比的一致率
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config_impact.impact_analyzer import ImpactAnalyzer, ConfigKey


def build_sample():
    a = ImpactAnalyzer()

    # ---- 配置项（含同名不同义：payment:timeout vs search:timeout）----
    for ns, name in [
        ("payment", "timeout"),        # 支付超时
        ("payment", "retry"),
        ("search", "timeout"),         # 搜索超时，名字相同、含义无关
        ("search", "index"),
        ("order", "pay_timeout"),      # 派生自 payment.timeout
        ("order", "pay_timeout_alert"),# 派生自 order.pay_timeout
        ("infra", "db.master.host"),
        ("infra", "db.slave.host"),
    ]:
        a.add_config(ns, name)

    # ---- 静态引用（服务/模块直接读配置）----
    a.add_static_ref("pay-gateway", "payment", "timeout",
                     "services/pay_gateway/config.py:12")
    a.add_static_ref("pay-gateway", "payment", "retry",
                     "services/pay_gateway/config.py:13")
    a.add_static_ref("refund-worker", "payment", "retry",
                     "services/refund_worker/main.py:40")
    a.add_static_ref("search-api", "search", "timeout",
                    "services/search_api/config.py:8")
    a.add_static_ref("search-indexer", "search", "index",
                     "services/search_indexer/loader.py:21")
    a.add_static_ref("checkout", "order", "pay_timeout",
                     "services/checkout/order.py:55")
    a.add_static_ref("alerting", "order", "pay_timeout_alert",
                     "services/alerting/rules.py:9")
    a.add_static_ref("order-db", "infra", "db.master.host",
                     "services/order_db/conn.py:17")

    # ---- 间接引用：配置派生链 payment.timeout -> pay_timeout -> pay_timeout_alert
    a.add_derive_ref("payment", "timeout",
                     "order", "pay_timeout",
                     "config/order.yaml: pay_timeout=${payment.timeout}*1000")
    a.add_derive_ref("order", "pay_timeout",
                     "order", "pay_timeout_alert",
                     "config/order.yaml: pay_timeout_alert=${pay_timeout}+500")

    # ---- 动态拼接引用 ----
    # region 在运行时取自环境变量，静态分析无法确定取值
    a.add_dynamic_ref(
        "multiregion-job", "infra", "db.*.host",
        '"db." + region + ".host"',
        "jobs/multiregion/conn.py:31",
    )
    # feature flag 名称由请求头拼接，必须进待确认清单
    a.add_dynamic_ref(
        "feature-gate", "payment", "flag.*",
        '"flag." + header["x-exp"]',
        "services/pay_gateway/experiment.py:46",
    )
    return a


# 人工标注真值：变更 payment:timeout 时，各集合应包含的消费方
GROUND_TRUTH = {
    "payment:timeout": {
        "direct": {"pay-gateway"},
        "indirect": {"checkout", "alerting"},
        "pending": set(),          # payment:timeout 不命中 db.*.host / flag.*
    },
    "infra:db.master.host": {
        "direct": {"order-db"},
        "indirect": set(),
        "pending": {"multiregion-job"},
    },
}


def print_report(a, changed):
    report = a.analyze(changed)
    title = f"变更配置项: {changed.fqn()}"
    print("=" * 72)
    print(title)
    print("=" * 72)

    print(f"\n[第1层] 直接引用（{len(report.direct)} 个）")
    for item in report.direct:
        print(f"  - {item.consumer:<18} 读取 {item.via.fqn()}"
              f"  @ {item.ref_source}")

    print(f"\n[第2层] 间接受影响（{len(report.indirect)} 个，含推导路径）")
    for item in report.indirect:
        chain = " -> ".join(item.path)
        print(f"  - {item.consumer:<18} 读取 {item.via.fqn()}")
        print(f"      推导路径: {chain}")
        print(f"      证据: {item.ref_source}")

    print(f"\n[待人工确认] 动态拼接引用（{len(report.pending)} 个，未并入上方清单）")
    if not report.pending:
        print("  （无）")
    for item in report.pending:
        print(f"  - {item.consumer:<18} 表达式 {item.expression!r}"
              f" -> 模式 {item.namespace}:{item.pattern}")
        print(f"      命中被变更项 {item.matched.fqn()}  @ {item.ref_source}")
    print()
    return report


def consistency(a, fqn, truth):
    ns, _, name = fqn.partition(":")
    report = a.analyze(ConfigKey(ns, name))
    got = {
        "direct": {i.consumer for i in report.direct},
        "indirect": {i.consumer for i in report.indirect},
        "pending": {i.consumer for i in report.pending},
    }
    results = {}
    total_hit = total_expected = 0
    for layer in ("direct", "indirect", "pending"):
        exp, actual = truth[layer], got[layer]
        hit = len(exp & actual)
        total_hit += hit
        total_expected += len(exp)
        results[layer] = (actual, exp, actual - exp, exp - actual)
    rate = total_hit / total_expected if total_expected else 1.0
    return results, rate


def main():
    a = build_sample()
    print("数据集统计:", a.stats())

    print_report(a, ConfigKey("payment", "timeout"))
    print_report(a, ConfigKey("infra", "db.master.host"))

    # ---- 同名不同义判定 ----
    print("=" * 72)
    print("同名不同义样例: payment:timeout 与 search:timeout")
    print("=" * 72)
    r1 = a.analyze(ConfigKey("payment", "timeout"))
    r2 = a.analyze(ConfigKey("search", "timeout"))
    pay_hit, search_hit = r1.affected_consumers(), r2.affected_consumers()
    print(f"  变更 payment:timeout -> {sorted(pay_hit)}")
    print(f"  变更 search:timeout  -> {sorted(search_hit)}")
    print(f"  两个影响集合交集: {sorted(pay_hit & search_hit)}"
          f" => {'判定正确：同名项被视为不同配置' if not (pay_hit & search_hit) else '误判！'}")
    print()

    # ---- 一致率 ----
    print("=" * 72)
    print("与人工标注真值的一致率")
    print("=" * 72)
    overall_rate = 1.0
    for fqn, truth in GROUND_TRUTH.items():
        results, rate = consistency(a, fqn, truth)
        overall_rate = min(overall_rate, rate)
        print(f"\n  {fqn}  一致率 = {rate:.0%}")
        for layer, (actual, exp, extra, missing) in results.items():
            mark = "OK" if not extra and not missing else "DIFF"
            print(f"    [{layer:8s}] {mark} 工具={sorted(actual)} 真值={sorted(exp)}")
            if extra:
                print(f"      误报(工具多出): {sorted(extra)}")
            if missing:
                print(f"      漏报(工具缺少): {sorted(missing)}")
    print(f"\n  总体一致率（取各场景最低值）= {overall_rate:.0%}")


if __name__ == "__main__":
    main()
