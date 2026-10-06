"""场景演示：打印重启时刻序列与无效重启判定（时间全部注入，无真实等待）。"""

from restart_policy import Action, Config, RestartSupervisor


def scenario_crash_loop():
    """连续起来就崩：指数退避 4 次后判定重启无效。"""
    sup = RestartSupervisor(Config(
        initial_backoff=1.0, backoff_multiplier=2.0, max_backoff=60.0,
        min_alive_duration=30.0, max_fast_failures=5))
    print("== 场景1：连续崩溃（每次只活 5s，最小存活 30s）==")
    t = 0.0
    for i in range(1, 6):
        sup.on_started(t)
        d = sup.on_exit(t + 5.0, code=1)
        if d.action is Action.RESTART:
            print(f"  第{i}次崩溃 t={t+5:5.1f}s  存活5.0s(<30s)  "
                  f"退避{d.until-(t+5):4.1f}s  ->  计划重启于 t={d.until:.1f}s")
            t = d.until
        else:
            print(f"  第{i}次崩溃 t={t+5:5.1f}s  -> 判定重启无效，终止自愈: {d.reason}")
    print(f"  重启时刻序列: {sup.restart_times}")
    print(f"  最终状态: {sup.state.value}\n")


def scenario_dependency_down():
    """依赖长期不可用：不重启服务，只对依赖退避探活。"""
    sup = RestartSupervisor(Config(
        min_alive_duration=30.0, max_fast_failures=5,
        dependency_available=False))
    print("== 场景2：依赖长期不可用 ==")
    sup.on_started(0.0)
    d = sup.on_exit(5.0, code=1)
    print(f"  t=5.0s 崩溃，检测到依赖不可用 -> {d.reason}，首次探活 t={d.until:.1f}s")
    t = d.until
    for _ in range(4):
        d = sup.on_dependency_probe(False, t)
        print(f"  t={t:6.1f}s 探活失败，下次探活 t={d.until:6.1f}s")
        t = d.until
    print(f"  ...期间服务重启次数: {sup.restart_count}（不重启）")
    d = sup.on_dependency_probe(True, t)
    print(f"  t={t:.1f}s 依赖恢复 -> 立即重启服务于 t={d.until:.1f}s")
    print(f"  重启时刻序列: {sup.restart_times}\n")


def scenario_healthy_then_crash():
    """健康存活后的崩溃：退避重置，不计无效重启。"""
    sup = RestartSupervisor(Config(min_alive_duration=30.0, max_fast_failures=5))
    print("== 场景3：健康存活 100s 后崩溃 ==")
    sup.on_started(0.0)
    sup.on_health_check(True, 31.0)
    print(f"  t=31.0s 存活满30s且健康检查通过 -> 判定存活(health_mark)")
    d = sup.on_exit(100.0, code=1)
    print(f"  t=100.0s 崩溃 -> 退避重置为 {d.until-100.0:.1f}s，重启于 t={d.until:.1f}s")
    print(f"  重启时刻序列: {sup.restart_times}")


if __name__ == "__main__":
    scenario_crash_loop()
    scenario_dependency_down()
    scenario_healthy_then_crash()
