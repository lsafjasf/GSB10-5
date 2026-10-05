"""volume_gc 的自测（unittest，仅标准库）。

运行：python3 test_volume_gc.py -v
覆盖情形：无引用 / 单个引用 / 多个引用 / 悬空引用 / 临时挂载 / 回收校验。
"""

import unittest

from volume_gc import VolumeGC


def fresh_gc():
    gc = VolumeGC()
    for c in ("c1", "c2", "c3"):
        gc.register_container(c)
    for v in ("v_none", "v_single", "v_multi", "v_dangling", "v_temp"):
        gc.add_volume(v)
    return gc


class TestNoReference(unittest.TestCase):
    def test_unreferenced_volume_is_reclaimed(self):
        gc = fresh_gc()
        self.assertFalse(gc.is_referenced("v_none"))
        self.assertEqual(gc.referrers("v_none"), [])
        result = gc.reclaim()
        self.assertIn("v_none", result.reclaimed)
        self.assertFalse(gc.has_volume("v_none"))


class TestSingleReference(unittest.TestCase):
    def test_single_referrer_keeps_volume(self):
        gc = fresh_gc()
        gc.mount("c1", "v_single", "/data")
        self.assertTrue(gc.is_referenced("v_single"))
        self.assertEqual(gc.referrers("v_single"), ["c1"])
        result = gc.reclaim()
        self.assertIn("v_single", result.kept)
        self.assertTrue(gc.has_volume("v_single"))

    def test_unmount_then_reclaim(self):
        gc = fresh_gc()
        ref = gc.mount("c1", "v_single", "/data")
        self.assertTrue(gc.unmount(ref))
        self.assertFalse(gc.is_referenced("v_single"))
        result = gc.reclaim()
        self.assertIn("v_single", result.reclaimed)


class TestMultipleReferences(unittest.TestCase):
    def test_multiple_referrers_listed(self):
        gc = fresh_gc()
        gc.mount("c1", "v_multi", "/a")
        gc.mount("c2", "v_multi", "/b")
        gc.mount("c3", "v_multi", "/c")
        self.assertEqual(gc.referrers("v_multi"), ["c1", "c2", "c3"])
        graph = gc.reference_graph()
        self.assertEqual(graph["v_multi"], ["c1", "c2", "c3"])
        result = gc.reclaim()
        self.assertIn("v_multi", result.kept)

    def test_partial_unmount_still_referenced(self):
        gc = fresh_gc()
        r1 = gc.mount("c1", "v_multi", "/a")
        gc.mount("c2", "v_multi", "/b")
        gc.unmount(r1)
        self.assertEqual(gc.referrers("v_multi"), ["c2"])
        self.assertTrue(gc.is_referenced("v_multi"))


class TestDanglingReference(unittest.TestCase):
    def test_dangling_does_not_protect(self):
        gc = fresh_gc()
        gc.mount("c1", "v_dangling", "/data")
        gc.destroy_container("c1")
        dangling = gc.dangling_refs("v_dangling")
        self.assertEqual(len(dangling), 1)
        self.assertEqual(dangling[0].container_id, "c1")
        self.assertFalse(gc.is_referenced("v_dangling"))
        result = gc.reclaim()
        self.assertEqual(len(result.pruned_dangling), 1)
        self.assertIn("v_dangling", result.reclaimed)
        self.assertEqual(gc.dangling_refs(), [])

    def test_prune_is_deterministic_and_idempotent(self):
        gc = fresh_gc()
        gc.mount("c1", "v_dangling", "/x")
        gc.mount("c2", "v_dangling", "/y")
        gc.destroy_container("c1")
        gc.destroy_container("c2")
        first = gc.prune_dangling()
        second = gc.prune_dangling()
        self.assertEqual(len(first), 2)
        self.assertEqual(second, [])

    def test_same_id_container_reregistered_revives_edge(self):
        gc = fresh_gc()
        gc.mount("c1", "v_dangling", "/data")
        gc.destroy_container("c1")
        self.assertFalse(gc.is_referenced("v_dangling"))
        # 确定规则：是否悬空只取决于“当前存活集合”。
        # 同 id 容器重新注册视为同一逻辑容器回归，其边恢复有效。
        gc.register_container("c1")
        self.assertTrue(gc.is_referenced("v_dangling"))
        # 但若悬空边已被 prune，则不会复活，必须重新 mount。
        gc.destroy_container("c1")
        gc.prune_dangling()
        gc.register_container("c1")
        self.assertFalse(gc.is_referenced("v_dangling"))


class TestTemporaryMount(unittest.TestCase):
    def test_temporary_mount_does_not_protect(self):
        gc = fresh_gc()
        gc.mount("c1", "v_temp", "/tmp/scratch", temporary=True)
        self.assertEqual(len(gc.temporary_refs("v_temp")), 1)
        self.assertFalse(gc.is_referenced("v_temp"))
        result = gc.reclaim()
        self.assertEqual(len(result.pruned_temporary), 1)
        self.assertIn("v_temp", result.reclaimed)

    def test_temporary_plus_persistent_is_kept(self):
        gc = fresh_gc()
        gc.mount("c1", "v_temp", "/tmp/scratch", temporary=True)
        gc.mount("c2", "v_temp", "/data")
        result = gc.reclaim()
        self.assertIn("v_temp", result.kept)
        self.assertEqual(gc.referrers("v_temp"), ["c2"])


class TestValidation(unittest.TestCase):
    def test_reclaim_result_passes_validation(self):
        gc = fresh_gc()
        gc.mount("c1", "v_single", "/data")
        gc.mount("c2", "v_multi", "/a")
        gc.mount("c3", "v_multi", "/b")
        gc.mount("c1", "v_dangling", "/d")
        gc.destroy_container("c1")
        gc.mount("c2", "v_temp", "/t", temporary=True)
        result = gc.reclaim()
        report = gc.validate(result.reclaimed, result.kept)
        self.assertTrue(report["passed"])
        for check in report["checks"]:
            self.assertTrue(check["passed"])
            if check["expectation"] == "kept":
                self.assertGreaterEqual(check["live_referrer_count"], 1)
            else:
                self.assertFalse(check["exists"])
                self.assertEqual(check["residual_edge_count"], 0)

    def test_validation_detects_lie(self):
        gc = fresh_gc()
        gc.mount("c1", "v_single", "/data")
        report = gc.validate(["v_single"], [])
        self.assertFalse(report["passed"])
        report = gc.validate([], ["v_none"])
        self.assertFalse(report["passed"])


class TestMountErrors(unittest.TestCase):
    def test_mount_requires_live_container_and_volume(self):
        gc = fresh_gc()
        with self.assertRaises(KeyError):
            gc.mount("ghost", "v_single", "/x")
        with self.assertRaises(KeyError):
            gc.mount("c1", "ghost_vol", "/x")
        gc.destroy_container("c1")
        with self.assertRaises(KeyError):
            gc.mount("c1", "v_single", "/x")

    def test_duplicate_mount_is_idempotent(self):
        gc = fresh_gc()
        r1 = gc.mount("c1", "v_single", "/data")
        r2 = gc.mount("c1", "v_single", "/data")
        self.assertEqual(r1, r2)
        self.assertEqual(len(gc.all_refs("v_single")), 1)


class TestPartialReclaim(unittest.TestCase):
    def test_reclaim_only_subset(self):
        gc = fresh_gc()
        result = gc.reclaim(only=["v_none", "nonexistent"])
        self.assertEqual(result.reclaimed, ["v_none"])
        self.assertEqual(result.unknown_volumes, ["nonexistent"])
        self.assertTrue(gc.has_volume("v_single"))


if __name__ == "__main__":
    unittest.main()
