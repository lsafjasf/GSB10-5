"""场景演示：在固定的注入时间下评估一组记录并落盘全部清单。

场景覆盖：
  r1-r3 全部到期、无引用、无暂停标记            -> 删除
  r4    到期但被 r6 引用                         -> 因引用幸免
  r5    到期且被一条悬空引用指向（目标不存在）    -> 悬空引用不构成保护，删除
  r6    自身很新，且引用 r4                       -> 未到期保留（引用方现存）
  r7    很旧但带暂停保留标记                     -> 因暂停标记幸免
  r8    时间戳落在容忍带内的未来（时钟轻微前拨）  -> 钳制为 now，按未到期保留
  r9    时间戳远超容忍带的未来（时钟严重不准）    -> 时间异常，保守保留
  r10   last_accessed_at 早于 created_at         -> 时间异常，保守保留
  r11   到期、被 r7（带暂停标记的记录）引用       -> 仍按被引用幸免
"""

import json

from .core import Policy, Record, evaluate
from .report_io import write_report_files

NOW = 1_000_000_000.0          # 测试注入的“当前时间”
RETENTION = 30 * 24 * 3600.0   # 30 天
TOLERANCE = 300.0              # 时钟容忍带 5 分钟

DAY = 86400.0


def build_records():
    old = NOW - 100 * DAY
    fresh = NOW - 1 * DAY
    return [
        Record("r1", old, old),
        Record("r2", old, old),
        Record("r3", old, NOW - 31 * DAY),
        Record("r4", old, old),
        Record("r5", old, old, references=()),
        Record("r6", fresh, fresh, references=("r4",)),
        Record("r7", old, old, retention_hold=True),
        Record("r8", NOW - 10 * DAY, NOW + 60.0),
        Record("r9", NOW + 10 * DAY, NOW + 10 * DAY),
        Record("r10", old, old - 5 * DAY),
        Record("r11", old, old, references=()),
    ]


# r7 引用 r11；r5 指向不存在的 ghost（悬空引用）。
def build_records_full():
    records = build_records()
    by_id = {r.id: r for r in records}
    records[4] = Record("r5", by_id["r5"].created_at, by_id["r5"].last_accessed_at,
                        references=("ghost",))
    records[6] = Record("r7", by_id["r7"].created_at, by_id["r7"].last_accessed_at,
                        references=("r11",), retention_hold=True)
    return records


def run(out_dir="out"):
    policy = Policy(retention_seconds=RETENTION,
                    clock_skew_tolerance_seconds=TOLERANCE)
    report = evaluate(build_records_full(), NOW, policy)
    paths = write_report_files(report, out_dir)

    print("=== 判定时间（注入）===", NOW)
    print("=== 汇总 ===")
    print(json.dumps(report.summary(), indent=2, ensure_ascii=False))

    print("\n=== 因被引用而幸免 ===")
    for e in report.spared_referenced:
        print(f"  {e.id}: {e.reason}; referrers={list(e.referrers)}")

    print("\n=== 时间异常（保守保留）===")
    for e in report.anomalies:
        print(f"  {e.id}: {e.reason}")

    print("\n=== 本次删除清单（含删除理由）===")
    for e in report.deletion_manifest:
        print(f"  {e.id}: {e.reason}")

    print("\n=== 悬空引用（引用完整性问题）===")
    for d in report.dangling_references:
        print(f"  {d.source_id} -> {d.target_id} (目标不存在)")

    print("\n清单文件：")
    for name, path in paths.items():
        print(f"  {name}: {path}")
    return report, paths


if __name__ == "__main__":
    run()
