"""Self-tests: correctness, permission isolation, permission-change semantics."""
import random
import time
import unittest

from completion_lib import CompletionIndex, PermissionChanged


def build_index(n_public=2000, n_secret=200, seed=7):
    rng = random.Random(seed)
    idx = CompletionIndex(limit=50)
    words = []
    for i in range(n_public):
        words.append(f"cmd_{rng.randrange(10**6):06d}_{i}")
    for i in range(n_secret):
        words.append(f"secret_{rng.randrange(10**6):06d}_{i}")
    for w in words:
        g = ["secret"] if w.startswith("secret_") else ["public"]
        if "secret" not in g and rng.random() < 0.1:
            g.append("ops")
        idx.add(w, g)
    idx.set_user_groups("alice", ["public", "ops"])
    idx.set_user_groups("root", ["public", "ops", "secret"])
    idx.set_user_groups("nobody", [])
    return idx


class TestFewCandidates(unittest.TestCase):
    def test_tiny_index(self):
        idx = CompletionIndex()
        idx.add("apple", ["public"])
        idx.add("apply", ["public"])
        idx.add("banana", ["public"])
        idx.set_user_groups("u", ["public"])
        self.assertEqual(idx.complete("u", "app"), ["apple", "apply"])
        self.assertEqual(idx.complete("u", "appl"), ["apple", "apply"])
        self.assertEqual(idx.complete("u", "apple"), ["apple"])
        self.assertEqual(idx.complete("u", "zzz"), [])
        self.assertEqual(idx.complete("u", ""), ["apple", "apply", "banana"])

    def test_empty_prefix_limit(self):
        idx = CompletionIndex(limit=2)
        for w in ["a1", "a2", "a3"]:
            idx.add(w, ["public"])
        idx.set_user_groups("u", ["public"])
        self.assertEqual(idx.complete("u", ""), ["a1", "a2"])


class TestManyCandidates(unittest.TestCase):
    def test_large_index_sorted_dedup(self):
        idx = build_index()
        res = idx.complete("alice", "cmd_", limit=100)
        self.assertEqual(res, sorted(set(res)))
        self.assertTrue(all(r.startswith("cmd_") for r in res))
        self.assertEqual(len(res), 100)

    def test_duplicate_across_groups_deduped(self):
        idx = CompletionIndex()
        idx.add("shared", ["g1", "g2"])
        idx.set_user_groups("u", ["g1", "g2"])
        self.assertEqual(idx.complete("u", "sh"), ["shared"])


class TestSensitiveNames(unittest.TestCase):
    def test_no_leak_any_prefix(self):
        idx = build_index()
        secret_names = {n for n, gs in idx._name_groups.items() if "secret" in gs}
        self.assertTrue(secret_names)
        leaks = 0
        # Probe with prefixes that deliberately chase secret names.
        probes = ["", "s", "se", "sec", "secret", "secret_", "cmd_", "c"]
        for s in list(secret_names)[:50]:
            probes.append(s[:8])
        for p in probes:
            for r in idx.complete("alice", p, limit=10**9):
                if r in secret_names:
                    leaks += 1
            for r in idx.complete("nobody", p, limit=10**9):
                if r in secret_names:
                    leaks += 1
        self.assertEqual(leaks, 0)

    def test_visible_set_exact(self):
        idx = build_index()
        vis = idx.visible_names("alice")
        self.assertFalse(any(n.startswith("secret_") for n in vis))
        self.assertTrue(all(n.startswith("secret_") for n in idx.visible_names("root")
                            if n.startswith("secret_")))


class TestPermissionChange(unittest.TestCase):
    def test_revoke_takes_effect_immediately(self):
        idx = build_index()
        before = idx.complete("root", "secret_", limit=10)
        self.assertTrue(before)
        idx.set_user_groups("root", ["public", "ops"])  # revoke secret
        after = idx.complete("root", "secret_", limit=10)
        self.assertEqual(after, [])

    def test_grant_takes_effect_immediately(self):
        idx = build_index()
        self.assertEqual(idx.complete("alice", "secret_", limit=10), [])
        idx.set_user_groups("alice", ["public", "ops", "secret"])
        self.assertTrue(idx.complete("alice", "secret_", limit=10))

    def test_cache_invalidated_on_change(self):
        idx = build_index()
        idx.complete("alice", "cmd_", limit=10)  # warm cache
        idx.set_user_groups("alice", [])  # revoke everything
        self.assertEqual(idx.complete("alice", "cmd_", limit=10), [])

    def test_streaming_detects_mid_iteration_change(self):
        idx = CompletionIndex(limit=10**9)
        for i in range(5000):
            idx.add(f"item_{i:05d}", ["public"])
        idx.set_user_groups("u", ["public"])
        gen = idx.complete_iter("u", "item_")
        first = next(gen)
        self.assertEqual(first, "item_00000")
        idx.set_user_groups("u", [])  # change permissions mid-stream
        with self.assertRaises(PermissionChanged):
            list(gen)
        # Deterministic recovery: re-query sees the new permissions.
        self.assertEqual(list(idx.complete_iter("u", "item_")), [])

    def test_single_result_is_version_consistent(self):
        idx = build_index()
        v0 = idx.version()
        res = idx.complete("alice", "cmd_", limit=20)
        self.assertEqual(idx.version(), v0)  # query itself never mutates
        self.assertTrue(res)


class TestPerfSmoke(unittest.TestCase):
    def test_query_latency_small(self):
        idx = build_index(n_public=200_000, n_secret=5_000)
        idx.complete("alice", "cmd_", limit=50)  # warm
        t0 = time.perf_counter()
        for _ in range(1000):
            idx.complete("alice", "cmd_12", limit=50)
        dt = (time.perf_counter() - t0) / 1000
        self.assertLess(dt, 0.005)  # 5ms/query is generous; typical <0.1ms


if __name__ == "__main__":
    unittest.main(verbosity=2)
