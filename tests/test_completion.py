"""自测：补全正确性、权限过滤、权限变更的确定性语义。

运行：python3 -m unittest tests.test_completion -v
"""
from __future__ import annotations

import threading
import time
import unittest

from completion import ACL, CompletionIndex


def oracle(items, prefix, user_groups, superuser=False):
    """参考实现：线性扫描 + 权限过滤，作为正确性基准。"""
    out = []
    for name, group in sorted(items):
        if not name.startswith(prefix):
            continue
        if group is None or superuser or group in user_groups:
            out.append((name, group))
    return out


def build_index_and_acl():
    items = [
        ("apple", None),
        ("apricot", None),
        ("avocado", None),
        ("banana", None),
        ("cherry", 1),
        ("cranberry", 2),
        ("date", 1),
        ("dragonfruit", None),
        ("fig", None),
        ("grape", 2),
        ("secret-admin-01", 900),
        ("secret-admin-02", 900),
        ("secret-token-01", 901),
    ]
    index = CompletionIndex(items)
    acl = ACL()
    acl.set_groups(grants={"alice": {1}, "bob": {1, 2}},
                   superusers={"root"})
    return items, index, acl


class TestSmallCandidateSet(unittest.TestCase):
    """候选很少时的基础行为。"""

    def setUp(self):
        self.items, self.index, self.acl = build_index_and_acl()

    def test_empty_prefix(self):
        res = self.index.complete("", "alice", self.acl, limit=100)
        self.assertEqual(res.names, tuple(
            n for n, g in sorted(self.items) if g in (None, 1)))

    def test_prefix_matching(self):
        for prefix in ("a", "ap", "apple", "sec", "zzz", "secret-admin"):
            res = self.index.complete(prefix, "root", self.acl, limit=100)
            self.assertEqual(res.items, tuple(oracle(self.items, prefix, {}, True)))

    def test_no_match_is_fast_and_empty(self):
        res = self.index.complete("zzz", "root", self.acl)
        self.assertEqual(res.names, ())
        self.assertFalse(res.truncated)
        self.assertEqual(res.scanned, 0)

    def test_limit_and_truncation(self):
        res = self.index.complete("", "root", self.acl, limit=3)
        self.assertEqual(len(res.items), 3)
        self.assertTrue(res.truncated)
        full = self.index.complete("", "root", self.acl, limit=100)
        self.assertEqual(res.items, full.items[:3])
        self.assertFalse(full.truncated)

    def test_prefix_end_boundary(self):
        # "ap" 不能匹配到 "avocado" 之类的串（验证上界 sentinel 正确）。
        res = self.index.complete("ap", "root", self.acl, limit=100)
        self.assertEqual(res.names, ("apple", "apricot"))


class TestPermissionFiltering(unittest.TestCase):
    """无权限的名字绝不出现。"""

    def setUp(self):
        self.items, self.index, self.acl = build_index_and_acl()

    def test_exhaustive_against_oracle(self):
        users = {"alice": {1}, "bob": {1, 2}, "carol": set(), "root": set()}
        supers = {"root"}
        prefixes = ["", "a", "c", "d", "g", "s", "secret", "secret-admin",
                    "secret-token", "apple", "banana"]
        for uid, groups in users.items():
            for prefix in prefixes:
                for limit in (1, 2, 5, 100):
                    res = self.index.complete(prefix, uid, self.acl, limit=limit)
                    expected = oracle(self.items, prefix, groups,
                                      uid in supers)[:limit]
                    self.assertEqual(res.items, tuple(expected),
                                     (uid, prefix, limit))

    def test_sensitive_names_never_leak(self):
        sensitive = {"secret-admin-01", "secret-admin-02", "secret-token-01"}
        for uid in ("alice", "bob", "carol"):
            res = self.index.complete("", uid, self.acl, limit=100)
            leaked = sensitive.intersection(res.names)
            self.assertFalse(leaked, f"{uid} 泄露了敏感名称: {leaked}")
            # 直接拿敏感前缀补全也一样
            res2 = self.index.complete("secret-", uid, self.acl, limit=100)
            self.assertFalse(sensitive.intersection(res2.names))

        # 未授权任何组的用户只能看到公开名称
        res = self.index.complete("", "carol", self.acl, limit=100)
        self.assertTrue(all(g is None for _, g in res.items))

    def test_superuser_sees_everything(self):
        res = self.index.complete("secret-", "root", self.acl, limit=100)
        self.assertEqual(res.names,
                         ("secret-admin-01", "secret-admin-02", "secret-token-01"))

    def test_unknown_user_sees_public_only(self):
        res = self.index.complete("", "nobody", self.acl, limit=100)
        self.assertTrue(all(g is None for _, g in res.items))

    def test_every_returned_item_is_visible(self):
        """对结果逐个复核可见性（权限在返回前已过滤的直接证据）。"""
        snap = self.acl.snapshot()
        res = self.index.complete("s", "bob", self.acl, limit=100)
        for name, group in res.items:
            self.assertTrue(snap.visible(group, "bob"),
                            f"返回了无权限名称 {name} (group={group})")


class TestPermissionChanges(unittest.TestCase):
    """权限在补全前后 / 进行中发生变化时的确定性处理。"""

    def setUp(self):
        self.items, self.index, self.acl = build_index_and_acl()

    def test_grant_and_revoke_take_effect(self):
        before = self.index.complete("secret-", "carol", self.acl)
        self.assertEqual(before.names, ())

        snap1 = self.acl.grant("carol", [901])
        after_grant = self.index.complete("secret-", "carol", self.acl)
        self.assertEqual(after_grant.names, ("secret-token-01",))
        self.assertEqual(after_grant.epoch, snap1.epoch)

        snap2 = self.acl.revoke("carol", [901])
        after_revoke = self.index.complete("secret-", "carol", self.acl)
        self.assertEqual(after_revoke.names, ())
        self.assertEqual(after_revoke.epoch, snap2.epoch)

    def test_session_blocks_writer_until_exit(self):
        """会话持读锁期间，权限更新被阻塞；会话内结果始终基于同一快照。"""
        with self.index.session(self.acl) as sess:
            epoch_before = sess.epoch
            self.assertEqual(sess.complete("secret-", "carol").names, ())
            done = []

            def writer():
                self.acl.grant("carol", [900])
                done.append(True)

            t = threading.Thread(target=writer)
            t.start()
            time.sleep(0.05)
            # 写者被活动会话阻塞，权限尚未生效
            self.assertEqual(done, [])
            self.assertEqual(sess.complete("secret-", "carol").names, ())
            self.assertEqual(sess.epoch, epoch_before)
        t.join(timeout=5)
        self.assertEqual(done, [True])
        # 会话结束后，新的补全立刻看到新权限
        res = self.index.complete("secret-", "carol", self.acl)
        self.assertEqual(res.names, ("secret-admin-01", "secret-admin-02"))

    def test_session_pins_epoch_via_update_afterward(self):
        """无并发地验证：会话绑定的 epoch 不受之后权限变化影响。"""
        with self.index.session(self.acl) as sess:
            pinned_epoch = sess.epoch
            r1 = sess.complete("secret-", "carol")
            self.assertEqual(r1.epoch, pinned_epoch)
            self.assertEqual(r1.names, ())
        # 会话结束后更新权限
        self.acl.grant("carol", [900])
        with self.index.session(self.acl) as sess2:
            self.assertEqual(sess2.epoch, pinned_epoch + 1)
            r2 = sess2.complete("secret-", "carol")
            self.assertEqual(r2.names, ("secret-admin-01", "secret-admin-02"))

    def test_update_is_atomic_and_blocks_inflight_scan(self):
        """在途补全期间发起的权限更新必须等待；结果不出现半旧半新。"""
        errors = []

        def reader():
            try:
                with self.index.session(self.acl) as sess:
                    epoch = sess.epoch
                    # 在会话存活期间反复补全，所有结果必须同 epoch
                    for _ in range(200):
                        r = sess.complete("secret-", "bob", limit=100)
                        assert r.epoch == epoch
                        groups = {g for _, g in r.items}
                        # bob 初始没有 900/901：secret- 下结果必须为空
                        assert groups <= {None, 1, 2}
                        assert r.names == ()
                    time.sleep(0.05)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        def writer():
            # 应当被阻塞直到读者会话结束，然后整体生效
            snap = self.acl.grant("bob", [900, 901])
            # 生效后 bob 的新补全立刻能看到全部敏感名
            r = self.index.complete("secret-", "bob", self.acl, limit=100)
            assert r.epoch == snap.epoch
            assert len(r.names) == 3

        t1 = threading.Thread(target=reader)
        t2 = threading.Thread(target=writer)
        t1.start()
        time.sleep(0.02)  # 确保读者先持锁
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)
        self.assertFalse(t1.is_alive() or t2.is_alive(), "发生死锁")
        self.assertEqual(errors, [])

    def test_concurrent_updates_serialize_epochs(self):
        """并发更新严格串行，epoch 单调且结果始终自洽。"""
        seen = []
        lock = threading.Lock()

        def writer(gid):
            for _ in range(20):
                snap = self.acl.grant("carol", [gid])
                with lock:
                    seen.append(snap.epoch)

        threads = [threading.Thread(target=writer, args=(g,))
                   for g in (900, 901)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        self.assertEqual(len(seen), 40)
        self.assertEqual(seen, sorted(seen))
        self.assertEqual(len(set(seen)), 40)  # 无重复 epoch


if __name__ == "__main__":
    unittest.main(verbosity=2)
