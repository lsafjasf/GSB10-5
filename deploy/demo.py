"""四个典型场景演示：完全线性、宽并行、成环拆解、含可选/弱依赖。

运行：python3 demo.py
"""
from deploy_order import DependencyGraph, STRONG, WEAK, OPTIONAL, plan, validate


def show(title, graph):
    print("=" * 70)
    print(title)
    print("=" * 70)
    p = plan(graph)
    print("\n分层部署结果（同层可并行）：")
    for i, layer in enumerate(p.layers):
        print(f"  L{i} ({len(layer)} 个): {', '.join(layer)}")

    if p.cyclic:
        print("\n检测到环，拆解方案：")
        for b in p.breaks:
            print(f"  环内组件: {', '.join(b.components)}")
            print(f"  断开边  : {', '.join(f'{a}↛{b2}（{a} 原本强依赖 {b2}）'
                                           for a, b2 in b.cut_edges)}")
            print(f"  影响组件: {', '.join(b.affected)}（需先降级启动，待依赖上线后恢复）")
            print("  分步部署:")
            for i, step in enumerate(b.steps):
                print(f"    步骤{i}: {', '.join(step)}")
        loops = [a for a, b in p.cut_edges if a == b]
        if loops:
            print(f"  自环边（直接断开）: {', '.join(loops)}")

    if p.weak_honored or p.weak_deferred:
        print("\n弱依赖处理：")
        for a, b in p.weak_honored:
            print(f"  采纳: {b} 先于 {a}")
        for a, b in p.weak_deferred:
            print(f"  放弃: {a} 弱依赖 {b}（与强依赖顺序冲突）")
    if p.optional:
        print("\n可选依赖（不参与排序，仅影响运行期特性）：")
        for a, b in p.optional:
            print(f"  {a} 可选 {b}")

    data = validate(graph, p)
    print("\n校验数据：")
    for key in ("components", "layers", "max_layer_width", "strong_total",
                "strong_satisfied", "strong_cut", "strong_violations",
                "weak_honored", "weak_deferred", "optional_ignored", "ok"):
        print(f"  {key}: {data[key]}")
    print()
    return p


def scenario_linear():
    g = DependencyGraph()
    chain = ["e", "d", "c", "b", "a"]  # e 依赖 d, ..., b 依赖 a
    for dep, pre in zip(chain, chain[1:]):
        g.add_dependency(dep, pre, STRONG)
    return show("场景 1：完全线性（a -> b -> c -> d -> e）", g)


def scenario_wide():
    g = DependencyGraph()
    g.add_component("base")
    for i in range(6):
        g.add_dependency(f"svc{i}", "base", STRONG)
        g.add_dependency("api-gateway", f"svc{i}", STRONG)
    return show("场景 2：宽并行（base -> 6 个服务 -> api-gateway）", g)


def scenario_cycle():
    g = DependencyGraph()
    g.add_dependency("auth", "user", STRONG)
    g.add_dependency("user", "cache", STRONG)
    g.add_dependency("cache", "auth", STRONG)
    g.add_dependency("gateway", "auth", STRONG)
    g.add_dependency("user", "db", STRONG)
    g.add_dependency("db", "base-os", STRONG)
    return show("场景 3：成环（auth / user / cache 互相依赖）", g)


def scenario_optional():
    g = DependencyGraph()
    g.add_dependency("app", "core", STRONG)
    g.add_dependency("core", "os", STRONG)
    g.add_dependency("app", "config-center", WEAK)   # 一致方向，采纳
    g.add_dependency("os", "app", WEAK)              # 与强依赖冲突，放弃
    g.add_dependency("app", "metrics-agent", OPTIONAL)
    g.add_dependency("app", "trace-ui", OPTIONAL)
    return show("场景 4：含可选依赖与弱依赖", g)


if __name__ == "__main__":
    scenario_linear()
    scenario_wide()
    scenario_cycle()
    scenario_optional()
