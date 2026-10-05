"""rlimit 自测：python3 -m unittest -v test_rlimit"""

import unittest

from rlimit import (
    DIMENSIONS, INF, MISSING, LimitExceeded, Node, explain, resolve_tree,
)


def build_demo_tree():
    #  root            cpu=4 mem=1024 pids=256 （disk/net 缺失=默认不限）
    #  ├─ a            cpu=2（收紧） mem 缺失（继承）
    #  │  └─ a1        cpu=8（越限） mem=512（收紧） pids=0（显式禁止）
    #  │     └─ a1x    cpu=1（在 4 下继续收紧） pids=32（对 0 越限）
    #  └─ b            cpu=0（显式禁止）
    #     └─ b1        cpu=2（对 0 越限，必须收敛到 0）
    root = Node("root", {"cpu_cores": 4, "memory_mb": 1024, "pids": 256})
    a = root.add(Node("a", {"cpu_cores": 2}))
    a1 = a.add(Node("a1", {"cpu_cores": 8, "memory_mb": 512, "pids": 0}))
    a1.add(Node("a1x", {"cpu_cores": 1, "pids": 32}))
    b = root.add(Node("b", {"cpu_cores": 0}))
    b.add(Node("b1", {"cpu_cores": 2}))
    return root


class TestInheritance(unittest.TestCase):

    def setUp(self):
        self.result, self.violations = resolve_tree(build_demo_tree())

    def test_root_default_for_missing_dim(self):
        # R5 根缺失维度默认不限
        self.assertEqual(self.result["root"]["disk_mb"][0], INF)
        chain = self.result["root"]["disk_mb"][1]
        self.assertEqual(chain[-1].action, "root-default")

    def test_single_layer_override_tighten(self):
        # 单层覆盖：root cpu=4 -> a cpu=2，生效 2
        self.assertEqual(self.result["root/a"]["cpu_cores"][0], 2)

    def test_single_layer_missing_inherits(self):
        # a 的 memory_mb 缺失：继承父级 1024，来源链整条复用父级
        val, chain = self.result["root/a"]["memory_mb"]
        self.assertEqual(val, 1024)
        root_chain = self.result["root"]["memory_mb"][1]
        self.assertIs(chain, root_chain)  # 共享不可变链对象

    def test_multi_layer_nesting(self):
        # 多层：a1x cpu：4(root) -> 2(a 收紧) -> 4 越限收敛(a1) -> 1(a1x 收紧) = 1
        val, chain = self.result["root/a/a1/a1x"]["cpu_cores"]
        self.assertEqual(val, 1)
        actions = [e.action for e in chain]
        self.assertEqual(actions, ["set", "set", "clamp", "set"])

    def test_child_larger_is_clamped_and_recorded(self):
        # R3/R4 子级 8 > 父级生效上限 2（root 4 经 a 收紧到 2）：clamp 到 2，并记录违规
        val, _ = self.result["root/a/a1"]["cpu_cores"]
        self.assertEqual(val, 2)
        v = next(x for x in self.violations
                 if x.path == "root/a/a1" and x.dim == "cpu_cores")
        self.assertEqual(v.requested, 8)
        self.assertEqual(v.parent_limit, 2)
        self.assertEqual(v.result, "clamped-to-parent")
        # b1 对零值上限越限：2 > 0 -> 收敛到 0
        self.assertEqual(self.result["root/b/b1"]["cpu_cores"][0], 0)

    def test_reject_mode_raises(self):
        with self.assertRaises(LimitExceeded):
            resolve_tree(build_demo_tree(), mode="reject")

    def test_zero_is_explicit_and_locks_downward(self):
        # R2/R6 零值：a1 pids=0（在 256 上收紧到禁止）
        self.assertEqual(self.result["root/a/a1"]["pids"][0], 0)
        # 下级 a1x 想用 32 突破 0：收敛到 0 并记违规
        self.assertEqual(self.result["root/a/a1/a1x"]["pids"][0], 0)
        self.assertTrue(
            any(x.path == "root/a/a1/a1x" and x.dim == "pids"
                for x in self.violations)
        )
        # b 的 cpu=0 且 b1 缺失也会继承到 0（零值沿链传递）
        b_chain = self.result["root/b"]["cpu_cores"][1]
        self.assertEqual(b_chain[-1].action, "zero")

    def test_missing_vs_zero_differ(self):
        # 缺失：保持父级原值；零值：强制为 0。二者判定不同
        root = Node("r", {"cpu_cores": 4})
        root.add(Node("missing_child", {"memory_mb": 1}))   # cpu 缺失
        root.add(Node("zero_child", {"cpu_cores": 0}))      # cpu 显式 0
        res, _ = resolve_tree(root)
        self.assertEqual(res["r/missing_child"]["cpu_cores"][0], 4)
        self.assertEqual(res["r/zero_child"]["cpu_cores"][0], 0)
        self.assertNotEqual(
            res["r/missing_child"]["cpu_cores"],
            res["r/zero_child"]["cpu_cores"],
        )

    def test_none_value_treated_as_missing(self):
        # 显式写 None 与 key 缺失等价
        root = Node("r", {"cpu_cores": 4})
        root.add(Node("c", {"cpu_cores": None}))
        res, _ = resolve_tree(root)
        self.assertEqual(res["r/c"]["cpu_cores"][0], 4)

    def test_explain_full_layer_chain(self):
        # explain 必须列出经过的每一层（含缺失继承层）
        entries = explain(build_demo_tree(), "root/a/a1", "memory_mb")
        self.assertEqual([e.path for e in entries],
                         ["root", "root/a", "root/a/a1"])
        self.assertEqual([e.raw for e in entries], [1024, MISSING, 512])
        self.assertEqual(entries[1].action, "inherit")
        self.assertEqual(entries[1].value, 1024)
        self.assertEqual(entries[2].value, 512)

    def test_all_dimensions_resolved(self):
        for path, eff in self.result.items():
            self.assertEqual(set(eff), set(DIMENSIONS), path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
