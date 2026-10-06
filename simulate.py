"""场景仿真：产出传播时间数据、日志量曲线、一致性校验、审计记录。

运行：python3 simulate.py   （产物写入 out/）
"""

import csv
import os
import sys

sys.path.insert(0, ".")
from dynlog import Manager

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
os.makedirs(OUT, exist_ok=True)


def scenario_propagation():
    """场景 1：单次调整在 8 个节点上的生效时刻。"""
    latencies = [0.0, 0.005, 0.01, 0.02, 0.05, 0.08, 0.12, 0.2]
    m = Manager()
    m.add_source("svc.pay", "INFO")
    for i, lat in enumerate(latencies):
        m.add_node("node-%d" % i, lat)

    v = m.adjust("svc.pay", "DEBUG", "zhangsan", "线上排障：支付超时")
    m.advance(0.25)  # 推进到全部送达

    rep = m.propagation_report(v)
    print("== 场景1 传播生效时间（版本 v%d，t=%.3fs 发布）==" % (v, rep["publish_time"]))
    print("%-8s %14s %14s %14s" % ("node", "channel_ms", "effective_s", "delay_ms"))
    rows = []
    for r in rep["rows"]:
        print("%-8s %14.0f %14.3f %14.1f" % (
            r["node"], r["channel_latency"] * 1000,
            r["effective_time"], r["effective_delay"] * 1000))
        rows.append([r["node"], r["channel_latency"], r["effective_time"],
                     r["effective_delay"]])
    with open(os.path.join(OUT, "propagation.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["node", "channel_latency_s", "effective_time_s",
                    "effective_delay_s"])
        w.writerows(rows)
    print("-> out/propagation.csv\n")


def scenario_volume_curve():
    """场景 2：级别拉满 -> 超限自动收敛 -> 流量回落 -> 自动恢复的日志量曲线。"""
    m = Manager(rate_limit=1000.0, burst=1000.0)
    m.add_source("svc.order", "INFO")
    m.add_node("local", 0.0)
    m.adjust("svc.order", "DEBUG", "lisi", "排障：订单链路追踪")

    curve = []  # (t, passed, dropped, filtered, effective_level)
    for sec in range(20):
        rate = 5000 if sec < 10 else 500  # t=10s 后流量自然回落
        p = d = f = 0
        for _ in range(10):               # 每秒 10 个切片，平滑限流
            m.advance(0.1)
            pp, dd, ff = m.emit("svc.order", "DEBUG", rate // 10)
            p, d, f = p + pp, d + dd, f + ff
        curve.append((sec, p, d, f, m.effective_level("svc.order")))

    with open(os.path.join(OUT, "volume_curve.csv"), "w", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["t_s", "passed", "dropped", "filtered", "effective_level"])
        w.writerows(curve)

    print("== 场景2 日志量曲线（上限 1000 条/s，| 通过 # 丢弃 x 过滤）==")
    for t, p, d, f, lvl in curve:
        bar_p = "|" * (p // 100)
        bar_d = "#" * (d // 100)
        bar_f = "x" * (f // 100)
        print("t=%2ds %-7s %s%s%s  (pass=%d drop=%d filt=%d)"
              % (t, lvl, bar_p, bar_d, bar_f, p, d, f))
    print("-> out/volume_curve.csv\n")

    with open(os.path.join(OUT, "audit.log"), "w") as fp:
        fp.write("# seq version t(s) source old->new actor reason\n")
        for r in m.audit:
            fp.write("%d v%d t=%.1f %s %s->%s by=%s %s\n" % (
                r.seq, r.version, r.timestamp, r.source,
                r.old_level, r.new_level, r.actor, r.reason))
    print("== 场景2 审计记录（共 %d 条，写入 out/audit.log）==" % len(m.audit))
    for r in m.audit:
        print("  #%d v%d t=%4.1fs %-9s %s->%s by=%s %s" % (
            r.seq, r.version, r.timestamp, r.source,
            r.old_level, r.new_level, r.actor, r.reason))
    print()


def scenario_consistency():
    """场景 3：50 次来回调整后的一致性校验。"""
    latencies = [0.0, 0.01, 0.03, 0.05, 0.1, 0.2]
    m = Manager()
    m.add_source("svc.auth", "INFO")
    for i, lat in enumerate(latencies):
        m.add_node("node-%d" % i, lat)

    for i in range(50):
        lvl = "ERROR" if i % 2 == 0 else "DEBUG"
        m.adjust("svc.auth", lvl, "ops-bot", "来回调第%d次" % i)
        m.advance(0.01)          # 刻意制造在途旧版本
    m.advance(1.0)               # 全部送达

    rep = m.check_consistency()
    lines = []
    lines.append("== 场景3 一致性校验（50 次来回调后调回 DEBUG）==")
    lines.append("节点数=%d 源数=%d 未生效/残留节点数=%d 结果=%s" % (
        rep["nodes"], rep["sources"], len(rep["mismatches"]),
        "CONSISTENT" if rep["ok"] else "INCONSISTENT"))
    for i in range(len(latencies)):
        node = m._nodes[i]
        ver, lvl = node.applied["svc.auth"]
        lines.append("  node-%d applied: v%d %s" % (i, ver, lvl))
    lines.append("控制器当前: v%d %s" % (
        m._sources["svc.auth"].version, m.effective_level("svc.auth")))
    text = "\n".join(lines)
    print(text + "\n")
    with open(os.path.join(OUT, "consistency.txt"), "w") as fp:
        fp.write(text + "\n")
    print("-> out/consistency.txt")


if __name__ == "__main__":
    scenario_propagation()
    scenario_volume_curve()
    scenario_consistency()
