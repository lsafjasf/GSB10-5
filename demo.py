"""演示：一棵小树的全维度解析，输出生效值、来源链与违约处理结果。"""
from rlimit import Node, Policy, LimitExceededError, format_chain, resolve_tree


def build():
    root = Node("root", {"cpu_cores": 16, "memory_mb": 65536, "pids": 4096})
    web = root.add(Node("web", {"memory_mb": 8192}))          # cpu 缺失 -> 继承
    web.add(Node("worker", {"cpu_cores": 64}))                # 超父级 -> 收敛
    web.add(Node("cache", {"pids": 0}))                       # 显式 0：最严格
    batch = root.add(Node("batch", {"cpu_cores": 4,
                                    "memory_mb": 131072}))    # 内存超父级 -> 收敛
    batch.add(Node("etl", {"cpu_cores": 2}))                  # 多层嵌套调小
    return root


def main():
    root = build()
    result = resolve_tree(root, Policy.CLAMP)

    print("== 生效值总览（CLAMP 模式）==")
    for path in sorted(result):
        dims = ", ".join(
            f"{d}={e.value if e.value is not None else '∞'}"
            + ("(收敛)" if e.clamped else "")
            for d, e in result[path].items())
        print(f"  {path:<24} {dims}")

    print("\n== 来源链示例 ==")
    for path, dim in [("/root/web/worker", "cpu_cores"),
                      ("/root/batch/etl", "cpu_cores"),
                      ("/root/web/cache", "pids"),
                      ("/root/batch", "memory_mb")]:
        print(format_chain(result[path][dim]))
        print()

    print("== REJECT 模式 ==")
    try:
        resolve_tree(build(), Policy.REJECT)
    except LimitExceededError as exc:
        print(f"  拒绝: {exc}")


if __name__ == "__main__":
    main()
