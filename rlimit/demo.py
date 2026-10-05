"""演示：规则表、来源链、越限处理、缺失 vs 零值。运行：python3 demo.py"""

from rlimit import DIMENSIONS, Node, explain, fmt_value, resolve_tree

RULE_TABLE = """\
规则表（所有维度均为 min-wins：数值越小越严格）
+-----------+--------+-------------------+-----------------------+-----------------------------+
| 维度      | 方向   | 缺失(None/未设置) | 零值(显式 0)          | 子级请求 > 父级生效上限     |
+-----------+--------+-------------------+-----------------------+-----------------------------+
| cpu_cores | min 胜 | 继承父级生效值    | 显式禁止，沿链锁定    | clamp: 收敛到父级上限并记录 |
| memory_mb | min 胜 | 继承父级生效值    | 显式禁止，沿链锁定    | reject: 抛 LimitExceeded    |
| pids      | min 胜 | 继承父级生效值    | 显式禁止，沿链锁定    | （两种模式可选，默认 clamp）|
| disk_mb   | min 胜 | 继承父级生效值    | 显式禁止，沿链锁定    | 根级缺失 -> 默认不限(inf)   |
| net_mbps  | min 胜 | 继承父级生效值    | 显式禁止，沿链锁定    |                             |
+-----------+--------+-------------------+-----------------------+-----------------------------+"""


def build():
    root = Node("root", {"cpu_cores": 4, "memory_mb": 1024, "pids": 256})
    a = root.add(Node("a", {"cpu_cores": 2}))
    a1 = a.add(Node("a1", {"cpu_cores": 8, "memory_mb": 512, "pids": 0}))
    a1.add(Node("a1x", {"cpu_cores": 1, "pids": 32}))
    b = root.add(Node("b", {"cpu_cores": 0}))
    b.add(Node("b1", {"cpu_cores": 2}))
    return root


def main():
    print(RULE_TABLE)
    root = build()
    result, violations = resolve_tree(root)

    print("\n== 生效值 ==")
    for path in sorted(result):
        eff = {d: fmt_value(v) for d, (v, _) in result[path].items()}
        print("  %-16s %s" % (path, eff))

    print("\n== 来源链（root/a/a1 的 memory_mb，逐层） ==")
    for e in explain(root, "root/a/a1", "memory_mb"):
        print("  层 %-12s raw=%-6s 动作=%-12s 过层后生效值=%s"
              % (e.path, e.raw, e.action, fmt_value(e.value)))

    print("\n== 来源链（root/a/a1/a1x 的 cpu_cores，含越限收敛层） ==")
    for e in explain(root, "root/a/a1/a1x", "cpu_cores"):
        print("  层 %-16s raw=%-6s 动作=%-14s 过层后生效值=%s"
              % (e.path, e.raw, e.action, fmt_value(e.value)))

    print("\n== 越限处理结果（clamp 模式） ==")
    for v in violations:
        print("  %s 维度 %s: 请求 %s > 父级上限 %s -> %s"
              % (v.path, v.dim, v.requested, v.parent_limit, v.result))

    print("\n== 缺失 vs 零值（同父 cpu=4 下的两个子节点） ==")
    r = Node("r", {"cpu_cores": 4})
    r.add(Node("missing_child", {"memory_mb": 1}))
    r.add(Node("zero_child", {"cpu_cores": 0}))
    res, _ = resolve_tree(r)
    print("  缺失子节点 cpu_cores 生效值: %s （继承父级）"
          % fmt_value(res["r/missing_child"]["cpu_cores"][0]))
    print("  零值子节点 cpu_cores 生效值: %s （显式禁止）"
          % fmt_value(res["r/zero_child"]["cpu_cores"][0]))


if __name__ == "__main__":
    main()
