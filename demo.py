"""End-to-end demonstration (time injected by a fake clock).

Run: python3 demo.py
Prints: per-event evidence list, SLA response data, state trajectories.
"""

from seir import (
    DEFAULT_SLA,
    Event,
    Incident,
    Level,
    Rule,
    RuleEngine,
)
from seir.incident import sla_report
from seir.rules import BOOST, FORCE_LEVEL, SUPPRESS
from seir.scoring import Signal


class Clock:
    def __init__(self, start=1_700_000_000.0):
        self.t = start

    def __call__(self):
        return self.t

    def at(self, seconds):
        self.t = 1_700_000_000.0 + seconds


LEVEL_CN = {Level.P1: "P1(紧急)", Level.P2: "P2(高)", Level.P3: "P3(中)",
            Level.P4: "P4(低)", Level.P5: "P5(信息)"}


def show_basis(inc, tag):
    a = inc.assessments[-1]
    b = a.breakdown
    print(f"[{tag}] 事件 {b.event_id} -> {LEVEL_CN[a.computed_level]}  "
          f"最终分={a.adjusted_score:.2f}")
    print(f"    融合分={b.fused:.2f}  资产系数=x{b.asset_factor}  "
          f"范围系数=x{b.scope_factor}")
    for c in b.contributions:
        print(f"    - 依据 {c.signal_id}({c.category}) 原始强度={c.raw_strength} "
              f"置信度={c.confidence} 有效={c.effective_strength:.1f} "
              f"边际贡献={c.marginal_points:.2f} 占比={c.share:.1%}")
    if a.applied_rules:
        print(f"    命中规则: {', '.join(a.applied_rules)}"
              f"{' (已抑制)' if a.suppressed else ''}")


def main():
    clk = Clock()

    print("=" * 78)
    print("场景一：单信号分级（依据清单）")
    print("=" * 78)
    inc1 = Incident("INC-A", clk)
    clk.at(0)
    inc1.update(Event("INC-A", asset_tier=5, affected=50, signals=[
        Signal("ALRT-9001", "ransomware_behavior", 88, 0.95),
    ]))
    show_basis(inc1, "单信号")

    print()
    print("=" * 78)
    print("场景二：多条弱信号叠加（单条都是 P5，融合后升级）")
    print("=" * 78)
    inc2 = Incident("INC-B", clk)
    clk.at(60)
    inc2.update(Event("INC-B", asset_tier=3, affected=3, signals=[
        Signal("W-01", "dns_beacon", 18, 0.7),
        Signal("W-02", "rare_process", 15, 0.8),
        Signal("W-03", "abnormal_login_time", 16, 0.75),
        Signal("W-04", "lateral_smb", 20, 0.6),
        Signal("W-05", "priv_escalation_attempt", 14, 0.85),
    ]))
    show_basis(inc2, "弱信号叠加")

    print()
    print("=" * 78)
    print("场景三：规则冲突（白名单抑制 vs 合规强制升级，按优先级裁决）")
    print("=" * 78)
    engine = RuleEngine([
        Rule("regulatory-pci", 200, lambda e: e.asset_tier >= 5, FORCE_LEVEL, Level.P2),
        Rule("known-scanner", 100, lambda e: e.id == "INC-C", SUPPRESS),
        Rule("vip-boost", 50, lambda e: e.affected >= 10, BOOST, 8),
    ])
    inc3 = Incident("INC-C", clk, rules=engine)
    clk.at(120)
    inc3.update(Event("INC-C", asset_tier=5, affected=20, signals=[
        Signal("SCAN-7", "vuln_scan", 8, 0.4),
    ]))
    show_basis(inc3, "规则冲突")

    print()
    print("=" * 78)
    print("场景四：事件升级/降级轨迹 + 响应时限（时钟注入）")
    print("=" * 78)
    inc4 = Incident("INC-D", clk)
    clk.at(0)
    inc4.update(Event("INC-D", 2, 1, [Signal("s1", "suspicious", 26)]))
    clk.at(300)
    inc4.update(Event("INC-D", 2, 1, [Signal("s1", "suspicious", 26),
                                     Signal("s2", "exploit", 60, 0.9)]))
    clk.at(420)
    inc4.update(Event("INC-D", 2, 12, [Signal("s1", "suspicious", 26),
                                      Signal("s2", "exploit", 60, 0.9),
                                      Signal("s3", "lateral_movement", 70, 0.95)]))
    clk.at(1500)
    inc4.respond()

    print("状态轨迹（时间为相对秒）：")
    for t in inc4.trajectory:
        old = "-" if t.from_level is None else LEVEL_CN[t.from_level]
        print(f"  t+{t.at - 1_700_000_000.0:>6.0f}s  {old} -> "
              f"{LEVEL_CN[t.to_level]:<10} 分数={t.score:6.2f} 原因={t.reason}")
    deadline = inc4.sla_deadline() - 1_700_000_000.0
    print(f"最终级别 {LEVEL_CN[inc4.level]}，响应耗时={inc4.response_time:.0f}s，"
          f"时限={DEFAULT_SLA.deadline(inc4.level):.0f}s，"
          f"截止点=t+{deadline:.0f}s，超时={inc4.sla_breached}")

    print()
    print("响应时限数据（全部事件）：")
    inc5 = Incident("INC-E", clk)
    clk.at(0)
    inc5.update(Event("INC-E", 5, 1, [Signal("q", "ransomware", 90)]))  # P1
    clk.at(2000)
    inc5.respond()                                                     # 超时
    inc6 = Incident("INC-F", clk)
    clk.at(0)
    inc6.update(Event("INC-F", 1, 1, [Signal("z", "info", 3)]))        # P5 未响应

    report = sla_report([inc1, inc2, inc3, inc4, inc5, inc6])
    print(f"  {'级别':<10}{'总数':>5}{'已响应':>7}{'超时':>5}{'超时率':>9}"
          f"{'平均耗时(s)':>13}{'时限(s)':>10}")
    for level in Level:
        s = report[level]
        print(f"  {LEVEL_CN[level]:<10}{s.total:>5}{s.responded:>7}"
              f"{s.breached:>5}{s.breach_rate:>8.0%} "
              f"{s.avg_response:>12.1f}{DEFAULT_SLA.deadline(level):>10.0f}")


if __name__ == "__main__":
    main()
