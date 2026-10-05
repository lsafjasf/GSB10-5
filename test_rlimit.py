"""rlimit 自测：覆盖单层、多层嵌套、子级更大、零值与缺失。"""
import unittest

from rlimit import (Action, DIMENSIONS, LimitExceededError, Node, Policy,
                    resolve_tree)


def eff(result, path, dim="cpu_cores"):
    return result[path][dim]


class TestSingleLevelOverride(unittest.TestCase):
    def test_child_tightens(self):
        root = Node("root", {"cpu_cores": 8}, [Node("child", {"cpu_cores": 4})])
        r = resolve_tree(root)
        e = eff(r, "/root/child")
        self.assertEqual(e.value, 4)
        self.assertEqual(e.source, "/root/child")
        self.assertFalse(e.clamped)
        self.assertEqual([c.action for c in e.chain], [Action.SET, Action.SET])
        self.assertEqual(e.requested, 4)


class TestMultiLevelNesting(unittest.TestCase):
    def test_min_wins_and_source_is_tightest(self):
        # root=8 -> a 缺失 -> b=2 -> c 缺失：生效 2，来源在 b
        root = Node("root", {"cpu_cores": 8})
        a = root.add(Node("a"))
        b = a.add(Node("b", {"cpu_cores": 2}))
        c = b.add(Node("c"))
        r = resolve_tree(root)
        e = eff(r, "/root/a/b/c")
        self.assertEqual(e.value, 2)
        self.assertEqual(e.source, "/root/a/b")
        self.assertEqual([x.action for x in e.chain],
                         [Action.SET, Action.INHERIT, Action.SET, Action.INHERIT])
        self.assertEqual(len(e.chain), 4)  # 来源链覆盖全部 4 层
        self.assertEqual([x.node for x in e.chain],
                         ["/root", "/root/a", "/root/a/b", "/root/a/b/c"])

    def test_middle_level_tightest(self):
        root = Node("root", {"memory_mb": 16384})
        a = root.add(Node("a", {"memory_mb": 4096}))
        b = a.add(Node("b", {"memory_mb": 8192}))  # 比父级大 -> 收敛
        r = resolve_tree(root)
        e = r["/root/a/b"]["memory_mb"]
        self.assertEqual(e.value, 4096)
        self.assertEqual(e.source, "/root/a")
        self.assertTrue(e.clamped)


class TestChildExceedsParent(unittest.TestCase):
    def build(self):
        root = Node("root", {"cpu_cores": 8})
        return root, root.add(Node("child", {"cpu_cores": 64}))

    def test_clamp_mode(self):
        root, _ = self.build()
        e = eff(resolve_tree(root, Policy.CLAMP), "/root/child")
        self.assertEqual(e.value, 8)            # 收敛到父级上限
        self.assertEqual(e.source, "/root")     # 约束来自父级
        self.assertTrue(e.clamped)
        self.assertEqual(e.requested, 64)       # 原始诉求保留在来源链
        self.assertEqual(e.chain[-1].action, Action.CLAMP)
        self.assertEqual(e.chain[-1].declared, 64)

    def test_reject_mode(self):
        root, _ = self.build()
        with self.assertRaises(LimitExceededError):
            resolve_tree(root, Policy.REJECT)

    def test_grandchild_cannot_escape_clamped_parent(self):
        root = Node("root", {"cpu_cores": 8})
        child = root.add(Node("child", {"cpu_cores": 64}))   # 收敛到 8
        gc = child.add(Node("gc", {"cpu_cores": 32}))        # 仍 > 8 -> 再收敛
        r = resolve_tree(root)
        self.assertEqual(eff(r, "/root/child").value, 8)
        e = eff(r, "/root/child/gc")
        self.assertEqual(e.value, 8)
        self.assertTrue(e.clamped)


class TestZeroVsMissing(unittest.TestCase):
    def test_zero_is_explicit_and_strictest(self):
        root = Node("root", {"cpu_cores": 8}, [
            Node("zero", {"cpu_cores": 0}),   # 显式 0
            Node("miss"),                     # 缺失
        ])
        r = resolve_tree(root)
        z, m = eff(r, "/root/zero"), eff(r, "/root/miss")
        self.assertEqual(z.value, 0)
        self.assertIsNotNone(z.value)         # 0 不是“不限”
        self.assertEqual(z.chain[-1].action, Action.SET)
        self.assertEqual(m.value, 8)          # 缺失 -> 继承父级
        self.assertEqual(m.chain[-1].action, Action.INHERIT)
        self.assertNotEqual(z.value, m.value) # 两者判定结果不同

    def test_zero_propagates_down(self):
        root = Node("root", {"pids": 0})
        child = root.add(Node("child"))
        r = resolve_tree(root)
        self.assertEqual(r["/root/child"]["pids"].value, 0)

    def test_zero_cannot_be_exceeded(self):
        root = Node("root", {"pids": 0})
        root.add(Node("child", {"pids": 10}))
        e = resolve_tree(root)["/root/child"]["pids"]
        self.assertEqual(e.value, 0)
        self.assertTrue(e.clamped)


class TestMissingAndUnlimited(unittest.TestCase):
    def test_root_missing_means_unlimited(self):
        root = Node("root")  # 根未声明 cpu_cores
        child = root.add(Node("child"))
        r = resolve_tree(root)
        e = eff(r, "/root/child")
        self.assertIsNone(e.value)            # None = 不限
        self.assertIsNone(e.source)
        self.assertEqual([c.action for c in e.chain],
                         [Action.ROOT_DEFAULT, Action.INHERIT])

    def test_child_can_limit_under_unlimited_root(self):
        root = Node("root")
        root.add(Node("child", {"cpu_cores": 2}))
        e = eff(resolve_tree(root), "/root/child")
        self.assertEqual(e.value, 2)          # 父级不限时子级可自由声明
        self.assertFalse(e.clamped)


class TestValidation(unittest.TestCase):
    def test_negative_rejected(self):
        with self.assertRaises(ValueError):
            resolve_tree(Node("root", {"cpu_cores": -1}))

    def test_unknown_dimension_rejected(self):
        with self.assertRaises(ValueError):
            resolve_tree(Node("root", {"gpu": 1}))

    def test_all_dimensions_resolved(self):
        r = resolve_tree(Node("root", {"cpu_cores": 1}))
        self.assertEqual(set(r["/root"]), set(DIMENSIONS))


if __name__ == "__main__":
    unittest.main(verbosity=2)
