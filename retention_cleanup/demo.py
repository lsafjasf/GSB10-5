"""确定性演示：打印判定结果，并把可追溯清单写成 JSON。

运行：python3 demo.py
所有时间均为注入的固定时刻 NOW（2026-10-06T00:00:00Z）。
"""

import json
import os

from engine import Reason, Record, RetentionCleaner, RetentionPolicy

NOW = 1_759_708_800.0
DAY = 86400.0
POLICY = RetentionPolicy(retention_seconds=90 * DAY, grace_seconds=0.0)

SCENARIOS = {
    "A_accurate_clock": {
        "now": NOW,
        "records": [
            Record("cfg-ancient", NOW - 400 * DAY),
            Record("cfg-old-ref", NOW - 400 * DAY, referenced_by=("svc-login",)),
            Record("svc-login", NOW - 2 * DAY),
            Record("cfg-legal-hold", NOW - 4000 * DAY, hold=True),
            Record("cfg-recent", NOW - 12 * DAY),
            Record("cfg-touched", NOW - 300 * DAY, NOW - 4 * DAY),
            Record("cfg-future-ts", NOW + 365 * DAY),
            Record("cfg-access-before-create", NOW - 30 * DAY, NOW - 90 * DAY),
            Record("cfg-boundary", NOW - 90 * DAY),
            Record("cfg-a", NOW - 500 * DAY, referenced_by=("cfg-b",)),
            Record("cfg-b", NOW - 500 * DAY, referenced_by=("cfg-a",)),
        ],
    },
    "B_clock_regressed_30d": {
        # 注入的时钟回拨 30 天（与场景 A 同一份数据对照）
        "now": NOW - 30 * DAY,
        "records": [
            Record("cfg-ancient", NOW - 400 * DAY),
            Record("cfg-old-ref", NOW - 400 * DAY, referenced_by=("svc-login",)),
            Record("svc-login", NOW - 2 * DAY),
            Record("cfg-legal-hold", NOW - 4000 * DAY, hold=True),
            Record("cfg-recent", NOW - 12 * DAY),
            Record("cfg-touched", NOW - 300 * DAY, NOW - 4 * DAY),
        ],
    },
    "C_integrity_violation": {
        "now": NOW,
        "records": [
            Record("cfg-target", NOW - 400 * DAY, referenced_by=("deleted-service",)),
            Record("cfg-junk", NOW - 400 * DAY),
        ],
    },
}


def print_scenario(name, scenario, cleaner, outdir):
    report = cleaner.evaluate(scenario["records"], scenario["now"], apply=True)
    print("=" * 72)
    print(f"场景 {name}  now={scenario['now']:.0f}  applied={report.applied}")
    if report.cleanup_aborted:
        print("  !! 清理被中止：引用完整性校验未通过，没有任何记录被删除")
        for v in report.violations:
            print(f"     - [{v.kind}] record={v.record_id} detail={v.detail}")
        print()
        return

    print(f"  总计 {report.total_records}  删除 {len(report.deleted)}  "
          f"被引用幸免 {len(report.spared_referenced)}  "
          f"暂停 {len(report.spared_hold)}  "
          f"活跃 {len(report.spared_active)}  "
          f"时间异常 {len(report.spared_anomaly)}")
    print()
    print("  清理清单（删除）:")
    for d in report.deleted:
        print(f"     - {d.record_id:<28} reason={','.join(d.reasons)} "
              f"idle={d.idle_seconds / DAY:.1f}d")
    print("  幸免清单（被引用）:")
    for d in report.spared_referenced:
        print(f"     - {d.record_id:<28} referrers={list(d.referrers)}")
    print("  幸免清单（暂停/活跃/异常）:")
    for d in report.spared_hold:
        print(f"     - {d.record_id:<28} [held] {','.join(d.reasons)}")
    for d in report.spared_active:
        print(f"     - {d.record_id:<28} [active] idle={d.idle_seconds / DAY:.1f}d")
    for d in report.spared_anomaly:
        print(f"     - {d.record_id:<28} [anomaly] {list(d.anomalies)}")
    print()

    path = os.path.join(outdir, f"manifest_{name}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report.manifest(), fh, ensure_ascii=False, indent=2)
    print(f"  可追溯清单已写入: {path}\n")


def main():
    outdir = os.path.join(os.path.dirname(__file__), "outputs")
    os.makedirs(outdir, exist_ok=True)
    cleaner = RetentionCleaner(POLICY)
    for name, scenario in SCENARIOS.items():
        print_scenario(name, scenario, cleaner, outdir)


if __name__ == "__main__":
    main()
