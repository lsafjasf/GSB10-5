"""session_lib 自测：首次登录 / 连续多次登录 / 并发登录 / 登录失败重试。

运行：python3 test_session_lib.py -v
每个用例会打印标识变化轨迹，便于人工核对确定行为。
"""

import threading
import unittest

from session_lib import (
    AuthenticationError,
    InvalidSessionError,
    SessionManager,
)


def short(sid: str) -> str:
    return sid[:8] + "…"


class TraceMixin:
    def trace(self, msg: str) -> None:
        print(f"    [轨迹] {msg}")


class TestFirstLogin(TraceMixin, unittest.TestCase):
    """场景 1：首次登录 —— 匿名标识在登录成功时被替换，旧标识立即失效。"""

    def test_first_login_rotates_id(self):
        mgr = SessionManager()
        anon = mgr.create_anonymous()
        self.trace(f"匿名会话标识      = {short(anon)}")

        new_id = mgr.login(anon, user_id="alice", credential_ok=True)
        self.trace(f"登录成功后新标识  = {short(new_id)}")

        self.assertNotEqual(anon, new_id, "登录成功必须更换标识")
        self.assertEqual(len(new_id), 32, "128 bit 随机数应为 32 个十六进制字符")

        with self.assertRaises(InvalidSessionError):
            mgr.validate(anon)
        self.trace(f"旧标识 {short(anon)} 访问被拒绝 (InvalidSessionError)")

        snap = mgr.validate(new_id)
        self.assertTrue(snap["authenticated"])
        self.assertEqual(snap["user_id"], "alice")
        self.assertEqual(snap["login_count"], 1)
        self.trace(f"新标识 {short(new_id)} 校验通过 authenticated=True user=alice")


class TestRepeatedLogins(TraceMixin, unittest.TestCase):
    """场景 2：连续多次登录 —— 每次都换号，形成单调失效链。"""

    def test_repeated_logins_rotate_every_time(self):
        mgr = SessionManager()
        ids = [mgr.create_anonymous()]
        self.trace(f"第 0 次（匿名）   = {short(ids[0])}")

        for round_no in range(1, 6):
            new_id = mgr.login(ids[-1], user_id="alice", credential_ok=True)
            ids.append(new_id)
            self.trace(f"第 {round_no} 次登录后   = {short(new_id)}")

        self.assertEqual(len(set(ids)), len(ids), "每次登录的标识都必须互不相同")
        for old in ids[:-1]:
            with self.assertRaises(InvalidSessionError):
                mgr.validate(old)
        self.trace("前 5 个历史标识全部访问被拒绝 (InvalidSessionError)")
        self.assertEqual(mgr.validate(ids[-1])["login_count"], 5)
        self.assertEqual(mgr.active_count, 1, "换号不得泄漏会话，始终只有 1 个活跃会话")


class TestConcurrentLogins(TraceMixin, unittest.TestCase):
    """场景 3：并发登录 —— 同一旧标识只有一个胜者，其余全部确定性失败。"""

    def test_concurrent_login_same_old_id_exactly_one_wins(self):
        for attempt in range(20):  # 重复 20 轮，验证行为确定
            mgr = SessionManager()
            anon = mgr.create_anonymous()
            results: dict[int, tuple[str, object]] = {}
            barrier = threading.Barrier(8)

            def worker(idx: int) -> None:
                barrier.wait()  # 尽量同时压向同一个旧标识
                try:
                    new_id = mgr.login(anon, user_id=f"user{idx}", credential_ok=True)
                    results[idx] = ("ok", new_id)
                except InvalidSessionError as exc:
                    results[idx] = ("rejected", exc)

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            winners = [r for r in results.values() if r[0] == "ok"]
            losers = [r for r in results.values() if r[0] == "rejected"]
            self.assertEqual(len(winners), 1, "并发登录同一旧标识，恰好一个成功")
            self.assertEqual(len(losers), 7, "其余 7 个必须因旧标识失效而被拒绝")
            self.assertEqual(mgr.active_count, 1)
            if attempt == 0:
                self.trace(f"8 线程并发登录同一旧标识 {short(anon)}")
                self.trace(f"胜者新标识 = {short(winners[0][1])}，败者 7 个均收到 InvalidSessionError")
                with self.assertRaises(InvalidSessionError):
                    mgr.validate(anon)
                self.trace(f"旧标识 {short(anon)} 访问被拒绝 (InvalidSessionError)")


class TestFailedLoginRetry(TraceMixin, unittest.TestCase):
    """场景 4：登录失败后重试 —— 失败不换号，成功后立即换号。"""

    def test_failed_login_keeps_id_then_success_rotates(self):
        mgr = SessionManager()
        anon = mgr.create_anonymous()
        self.trace(f"匿名会话标识        = {short(anon)}")

        for i in range(3):
            with self.assertRaises(AuthenticationError):
                mgr.login(anon, user_id="alice", credential_ok=False)
            self.assertEqual(mgr.validate(anon)["failed_attempts"], i + 1)
        self.trace(f"3 次失败登录后标识仍为 {short(anon)}（未换号，failed_attempts=3）")

        new_id = mgr.login(anon, user_id="alice", credential_ok=True)
        self.assertNotEqual(anon, new_id)
        self.trace(f"第 4 次登录成功，换号为 {short(new_id)}")

        with self.assertRaises(InvalidSessionError):
            mgr.validate(anon)
        self.trace(f"旧标识 {short(anon)} 访问被拒绝 (InvalidSessionError)")

        snap = mgr.validate(new_id)
        self.assertEqual(snap["failed_attempts"], 3, "失败计数随会话数据一起迁移")
        self.assertEqual(snap["login_count"], 1)


class TestLogout(TraceMixin, unittest.TestCase):
    def test_logout_invalidates(self):
        mgr = SessionManager()
        sid = mgr.login(mgr.create_anonymous(), "alice", True)
        mgr.logout(sid)
        with self.assertRaises(InvalidSessionError):
            mgr.validate(sid)
        self.assertEqual(mgr.active_count, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
