"""演示：分级依据清单、响应时限数据、升降级状态轨迹。

运行：python3 demo.py
"""
from seclib import Asset, Event, Level, SecurityEngine, Signal
from seclib.engine import SLA_SECONDS


class FakeClock:
    def __init__(self, start: float = 1_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def show_grade(engine, event):
    result = engine.ingest(event)
    print(f"\n== 事件 {event.event_id} 初评: {result.level.name} (总分 {result.score}) ==")
    print(f"  {'维度':<6} {'来源':<14} {'原始值':>6} {'加权分':>8}  说明")
    for c in result.contributions:
        print(f"  {c.dimension:<6} {c.source:<14} {c.raw:>6.1f} {c.weighted:>8.3f}  {c.note}")
    return result


def show_trajectory(engine, event_id):
    print(f"\n-- 事件 {event_id} 状态轨迹 --")
    for t in engine.state_of(event_id).trajectory:
        print(f"  t={t.ts:>12.0f}  {t.old_level.name}->{t.new_level.name}  [{t.rule}] {t.reason}")


def main():
    clock = FakeClock()
    engine = SecurityEngine(clock=clock)

    print("响应时限绑定: " + ", ".join(f"{lv.name}={SLA_SECONDS[lv]}s" for lv in Level))

    # 场景 1：单条强信号
    show_grade(engine, Event("E1", Asset("web-01", 3), 1, [Signal("rce-exploit", 5)]))
    # 场景 2：多条弱信号叠加（同资产同范围，比 E1 更严重）
    show_grade(engine, Event("E2", Asset("db-core", 3), 1, [
        Signal("login-anomaly", 2), Signal("priv-escalation", 2),
        Signal("data-exfil-dns", 2), Signal("off-hours-access", 2),
    ]))

    # 场景 3：事件升级轨迹（新证据 -> 范围扩大 -> SLA 预警）
    show_grade(engine, Event("E3", Asset("oa-02", 3), 1, [Signal("scan", 2)]))
    engine.add_signal("E3", Signal("c2-beacon", 4))
    engine.expand_scope("E3", 4)
    clock.advance(SLA_SECONDS[engine.state_of("E3").current.level] * 0.5 + 1)
    engine.tick()
    show_trajectory(engine, "E3")

    # 场景 4：规则冲突（证据失效 vs SLA 预警，升级优先）后降级
    show_grade(engine, Event("E4", Asset("mail-01", 3), 1,
                             [Signal("phish-url", 4), Signal("spam-report", 1)]))
    clock.advance(SLA_SECONDS[Level.P2] * 0.5 + 1)
    engine.expire_signals("E4", ["spam-report"])
    show_trajectory(engine, "E4")

    # 响应时限核对
    print("\n== 响应时限核对 ==")
    print(f"  {'事件':<5} {'级别':<4} {'时限(s)':>8} {'实耗(s)':>8}  是否超时")
    clock.advance(120)
    engine.acknowledge("E1")
    clock.advance(300)
    engine.acknowledge("E2")
    clock.advance(60_000)
    engine.acknowledge("E3")
    engine.acknowledge("E4")
    for eid in ("E1", "E2", "E3", "E4"):
        state = engine.state_of(eid)
        used = engine.response_seconds(eid)
        sla = engine.sla_for(eid)
        print(f"  {eid:<5} {state.current.level.name:<4} {sla:>8} {used:>8.0f}  "
              f"{'超时' if engine.is_overdue(eid) else '达标'}")
    print(f"\n汇总: {engine.stats()}")


if __name__ == "__main__":
    main()
