"""演示：解析轨迹 / 环报告 / 延迟取值时机 / 失败回收。运行: python3 demo.py"""
from di import (
    CircularDependencyError,
    ConstructionError,
    Container,
    LazyCycleError,
    lazy,
)


def banner(title):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def scenario_linear_chain():
    banner("场景 1: 线性链 a -> b -> c（无环，后序构造）")
    c = Container()
    c.register("a", lambda b: f"A({b})", deps=("b",))
    c.register("b", lambda cc: f"B({cc})", deps=("c",))
    c.register("c", lambda: "C")
    print("结果:", c.resolve("a"))
    print(c.trace.render())


def scenario_diamond():
    banner("场景 2: 菱形依赖 top -> {l, r} -> b（共享依赖只构造一次）")
    c = Container()
    c.register("top", lambda l, r: "TOP", deps=("l", "r"))
    c.register("l", lambda b: "L", deps=("b",))
    c.register("r", lambda b: "R", deps=("b",))
    c.register("b", lambda: "B")
    c.resolve("top")
    print(c.trace.render())


def scenario_cycle():
    banner("场景 3: 循环依赖（两个环 + 自环，报告全部环成员，构造前失败）")
    c = Container()
    c.register("root", lambda a, d, s: None, deps=("a", "d", "s"))
    c.register("a", lambda b: None, deps=("b",))
    c.register("b", lambda cc: None, deps=("c",))
    c.register("c", lambda a: None, deps=("a",))   # 环1: a->b->c->a
    c.register("d", lambda e: None, deps=("e",))
    c.register("e", lambda d: None, deps=("d",))   # 环2: d->e->d
    c.register("s", lambda s: None, deps=("s",))   # 环3: 自环
    try:
        c.resolve("root")
    except CircularDependencyError as exc:
        print("环报告:")
        print(exc.report.render())
        print("\n轨迹:")
        print(c.trace.render())


def scenario_lazy():
    banner("场景 4: 延迟注入解开循环 a -(lazy)-> b -> a，并展示取值时机")
    c = Container()
    c.register("a", lambda b_ref: ("A", b_ref), deps=(lazy("b"),))
    c.register("b", lambda a: ("B", a), deps=("a",))
    inst_a = c.resolve("a")
    print("resolve('a') 完成，此时 b 尚未构造（上面轨迹中没有 b 的构造步骤）")
    print(c.trace.render())
    print("\n--- 现在第一次访问 b_ref.get()，才真正构造 b ---")
    b_val, a_of_b = inst_a[1].get()
    print("b =", b_val, "| b 持有的 a 是同一实例:", a_of_b is inst_a)
    print(c.trace.render())

    banner("场景 4b: 延迟依赖在构造期被取值 -> 动态环报错")
    c2 = Container()
    c2.register("a", lambda b_ref: b_ref.get(), deps=(lazy("b"),))
    c2.register("b", lambda a: "B", deps=("a",))
    try:
        c2.resolve("a")
    except LazyCycleError as exc:
        print("动态环:", exc)
        print(c2.trace.render())


def scenario_failure():
    banner("场景 5: 构造中途失败 -> 已构造实例按逆序安全回收")
    c = Container()
    log = []
    c.register("db", lambda: "DB", disposer=lambda i: log.append(f"close {i}"))
    c.register("cache", lambda: "CACHE", disposer=lambda i: log.append(f"close {i}"))
    c.register("repo", lambda db, cache: "REPO", deps=("db", "cache"),
               disposer=lambda i: log.append(f"close {i}"))
    def boom(repo):
        raise RuntimeError("磁盘已满")
    c.register("service", boom, deps=("repo",))
    try:
        c.resolve("service")
    except ConstructionError as exc:
        print("失败:", exc)
    print("清理结果(disposer 调用顺序):", log)
    print("容器内残留实例:", dict(c._instances), "(应为空)")
    print(c.trace.render())

    banner("场景 5b: disposer 自身抛错不中断其余回收")
    c2 = Container()
    done = []
    c2.register("x", lambda: "X",
                disposer=lambda i: (_ for _ in ()).throw(ValueError("dispose 失败")))
    c2.register("y", lambda: "Y", disposer=lambda i: done.append("y cleaned"))
    c2.register("z", lambda x, y: (_ for _ in ()).throw(RuntimeError("z 构造失败")),
                deps=("x", "y"))
    try:
        c2.resolve("z")
    except ConstructionError as exc:
        print("失败:", exc)
    print("清理结果:", done, "| 残留实例:", dict(c2._instances))
    print(c2.trace.render())


if __name__ == "__main__":
    scenario_linear_chain()
    scenario_diamond()
    scenario_cycle()
    scenario_lazy()
    scenario_failure()
