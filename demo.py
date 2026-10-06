"""端到端场景演示：线上排障 -> 级别调到底 -> 超限自动收敛 -> 调回 -> 一致性校验。

场景：1000 个日志源（10 个高频源 base=100/s，990 个普通源 base=2/s），
默认 INFO。t=2s 时 oncall 把全部源调到 DEBUG，日志量瞬间超过上限
cap=9000 条/周期，VolumeGuard 自动逐级收敛；t=12s 时调回 INFO，
守卫覆盖自动释放，最后做全量一致性校验。

产出（out/ 目录）：
  propagation.csv   每次变更每个节点的生效时刻与延迟
  volume_curve.csv  每周期的总日志量 / 上限 / 被守卫收敛的源数
  volume_curve.txt  日志量 ASCII 曲线
  consistency.txt   三个时点的一致性校验数据
  audit.log         全量审计记录（谁、何时、哪个源、调成什么级别）
"""

import os
import zlib

from dynlog import DEBUG, INFO, LogLevelManager, VolumeGuard, Workload, level_name

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")

SOURCE_COUNT = 1000
HOT_COUNT = 10
CAP = 9000.0
DT = 0.1
T_DEBUG = 2.0
T_REVERT = 12.0
T_END = 18.0


def delay_fn(source):
    """确定性的每节点传播延迟：1~50ms（用 crc32 模拟节点网络抖动）。"""
    return (zlib.crc32(source.encode("utf-8")) % 50 + 1) / 1000.0


def percentile(sorted_values, p):
    rank = (len(sorted_values) - 1) * p
    low = int(rank)
    high = min(low + 1, len(sorted_values) - 1)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (rank - low)


def render_curve(curve, cap, width=64):
    peak = max(total for _, total, _ in curve)
    scale = width / peak
    cap_pos = int(cap * scale)
    lines = []
    for t, total, guarded in curve:
        bar_len = int(total * scale)
        bar = [" "] * (width + 2)
        for i in range(min(bar_len, width + 1)):
            bar[i] = "#"
        if 0 <= cap_pos <= width:
            bar[cap_pos] = "|"
        lines.append(
            "t=%5.1fs |%s| %8.0f  guarded=%d" % (t, "".join(bar), total, guarded)
        )
    header = "cap=%.0f  peak=%.0f (%.1fx cap)  '#'=volume  '|'=cap" % (
        cap,
        peak,
        peak / cap,
    )
    return header + "\n" + "\n".join(lines)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    applies = []
    manager = LogLevelManager(
        delay_fn=delay_fn,
        on_apply=lambda name, version, requested, guard, at: applies.append(
            (name, version, requested, guard, at)
        ),
    )
    base_rates = {}
    for index in range(SOURCE_COUNT):
        name = "src-%04d" % index
        manager.add_source(name, INFO)
        base_rates[name] = 100.0 if index < HOT_COUNT else 2.0

    workload = Workload(manager, base_rates)
    guard = VolumeGuard(manager, cap=CAP, cooldown=3)

    curve = []
    consistency_snapshots = []
    debug_versions = None
    revert_versions = None
    t_debug_actual = t_revert_actual = None

    while manager.now < T_END - 1e-9:
        manager.advance(DT)
        now = manager.now

        if debug_versions is None and now >= T_DEBUG - 1e-9:
            t_debug_actual = now
            debug_versions = {}
            for name in manager.sources():
                debug_versions[name] = manager.set_level(
                    name, DEBUG, operator="oncall-zhang",
                    reason="INC-1024 checkout 5xx spike",
                )
        if revert_versions is None and now >= T_REVERT - 1e-9:
            t_revert_actual = now
            revert_versions = {}
            for name in manager.sources():
                revert_versions[name] = manager.set_level(
                    name, INFO, operator="oncall-zhang",
                    reason="INC-1024 resolved, restore default",
                )
            consistency_snapshots.append((now, manager.consistency_check()))

        volumes = workload.tick()
        total = guard.observe(volumes)
        guarded = sum(
            1 for name in manager.sources() if manager.state(name).guard_level is not None
        )
        curve.append((now, total, guarded))

        if abs(now - (T_REVERT + DT)) < 1e-9 or abs(now - (T_REVERT + 0.5)) < 1e-9:
            consistency_snapshots.append((now, manager.consistency_check()))

    final_report = manager.consistency_check()
    consistency_snapshots.append((manager.now, final_report))

    def latencies(versions, target_level, t0):
        result = {}
        for name, version, requested, _guard_level, at in applies:
            if version == versions.get(name) and requested == target_level and at >= t0 - 1e-9:
                if name not in result:
                    result[name] = (at, at - t0)
        return result

    debug_lat = latencies(debug_versions, DEBUG, t_debug_actual)
    revert_lat = latencies(revert_versions, INFO, t_revert_actual)

    prop_path = os.path.join(OUT_DIR, "propagation.csv")
    with open(prop_path, "w", encoding="utf-8") as fh:
        fh.write("change,source,requested_at,applied_at,latency_ms\n")
        for change, table, t0 in (
            ("INFO->DEBUG", debug_lat, t_debug_actual),
            ("DEBUG->INFO", revert_lat, t_revert_actual),
        ):
            for name in sorted(table):
                applied_at, latency = table[name]
                fh.write(
                    "%s,%s,%.3f,%.3f,%.1f\n"
                    % (change, name, t0, applied_at, latency * 1000.0)
                )

    def stats(table, label):
        values = sorted(lat for _, lat in table.values())
        line = (
            "%s: nodes=%d min=%.1fms p50=%.1fms p95=%.1fms p99=%.1fms max=%.1fms"
            % (
                label,
                len(values),
                values[0] * 1000,
                percentile(values, 0.50) * 1000,
                percentile(values, 0.95) * 1000,
                percentile(values, 0.99) * 1000,
                values[-1] * 1000,
            )
        )
        print(line)
        return line

    print("== 传播生效时间（每节点延迟，详见 out/propagation.csv） ==")
    stats_lines = [
        stats(debug_lat, "INFO->DEBUG"),
        stats(revert_lat, "DEBUG->INFO"),
    ]

    curve_csv = os.path.join(OUT_DIR, "volume_curve.csv")
    with open(curve_csv, "w", encoding="utf-8") as fh:
        fh.write("time_s,total_volume,cap,guarded_sources\n")
        for t, total, guarded in curve:
            fh.write("%.1f,%.0f,%.0f,%d\n" % (t, total, CAP, guarded))

    chart = render_curve(curve[::2], CAP)
    with open(os.path.join(OUT_DIR, "volume_curve.txt"), "w", encoding="utf-8") as fh:
        fh.write(chart + "\n")
    print("\n== 日志量曲线（每 0.2s 一个采样，完整数据见 out/volume_curve.csv） ==")
    print(chart)

    cons_path = os.path.join(OUT_DIR, "consistency.txt")
    with open(cons_path, "w", encoding="utf-8") as fh:
        for t, report in consistency_snapshots:
            fh.write("check@t=%.1fs %s\n" % (t, report.summary()))
            for mismatch in report.mismatches[:5]:
                fh.write("  mismatch: %s\n" % mismatch)
        leftover = [
            name
            for name in manager.sources()
            if manager.state(name).guard_level is not None
        ]
        fh.write("guard overrides remaining: %d\n" % len(leftover))
        fh.write(
            "requested==effective on all %d sources: %s\n"
            % (final_report.total, final_report.ok)
        )
    print("\n== 一致性校验（out/consistency.txt） ==")
    with open(cons_path, encoding="utf-8") as fh:
        print(fh.read().strip())

    audit_path = os.path.join(OUT_DIR, "audit.log")
    manager.audit.dump(audit_path)
    records = manager.audit.records
    print("\n== 审计记录（共 %d 条，全量见 out/audit.log，示例首尾各 3 条） ==" % len(records))
    for record in records[:3] + records[-3:]:
        print(record.line())

    print("\n== 守卫统计 ==")
    print("escalations=%d relaxations=%d" % (guard.escalations, guard.relaxations))

    summary_path = os.path.join(OUT_DIR, "summary.txt")
    with open(summary_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(stats_lines) + "\n")
        fh.write("audit_records=%d\n" % len(records))
        fh.write("guard escalations=%d relaxations=%d\n" % (guard.escalations, guard.relaxations))
        fh.write("final consistency: %s\n" % final_report.summary())


if __name__ == "__main__":
    main()
