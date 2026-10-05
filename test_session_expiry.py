"""session_expiry 自测（python3 -m unittest -v）。

时间全部由测试注入，不依赖系统时钟。
覆盖：持续活跃、长期空闲、两个边界卡点、时钟回拨、活跃超绝对期限失效。
"""

import unittest

from session_expiry import Session, SessionStore

IDLE = 10       # 空闲超时 10s
ABSOLUTE = 30   # 绝对寿命 30s


class TestIdleAndAbsolute(unittest.TestCase):
    def test_long_idle_expires(self):
        """长期空闲：不续期，空闲超时后失效，两个剩余时间都要报。"""
        s = Session(now=0, idle_timeout=IDLE, absolute_timeout=ABSOLUTE)
        ri, ra, expired = s.remaining(9)
        self.assertEqual((ri, ra, expired), (1, 21, False))
        ri, ra, expired = s.remaining(11)
        self.assertEqual((ri, ra, expired), (-1, 19, True))

    def test_continuous_activity_renews_idle(self):
        """持续活跃：每次续期把空闲起点滑到 now，空闲永不过期。"""
        s = Session(now=0, idle_timeout=IDLE, absolute_timeout=ABSOLUTE)
        for t in (5, 10, 15, 20, 25, 29):
            self.assertTrue(s.touch(t), f"t={t} 应续期成功")
            ri, ra, expired = s.remaining(t)
            self.assertFalse(expired)
            self.assertEqual(ri, IDLE)          # 空闲窗口被重置为完整 10s
            self.assertEqual(ra, ABSOLUTE - t)  # 绝对剩余只减不增

    def test_active_session_still_dies_at_absolute_limit(self):
        """持续活跃但超过绝对期限：必须失效。"""
        s = Session(now=0, idle_timeout=IDLE, absolute_timeout=ABSOLUTE)
        for t in range(5, 30, 5):
            self.assertTrue(s.touch(t))
        # t=30 恰为绝对过期时刻：即使 1 秒前还活跃，也判定失效
        ri, ra, expired = s.remaining(30)
        self.assertEqual((ri, ra), (5, 0))   # 空闲还剩 5s，绝对已归零
        self.assertTrue(expired)
        self.assertFalse(s.touch(30))        # 续期被拒绝
        self.assertFalse(s.touch(31))

    def test_absolute_expiry_never_slides(self):
        """续期不改变绝对过期时刻。"""
        s = Session(now=0, idle_timeout=IDLE, absolute_timeout=ABSOLUTE)
        s.touch(7)
        s.touch(15)
        self.assertEqual(s.absolute_expiry, ABSOLUTE)
        self.assertEqual(s.idle_expiry, 15 + IDLE)


class TestBoundaries(unittest.TestCase):
    def test_idle_boundary_exact(self):
        """刚好卡在空闲边界：now == idle_expiry 视为已过期，续期被拒绝。"""
        s = Session(now=0, idle_timeout=IDLE, absolute_timeout=ABSOLUTE)
        self.assertFalse(s.is_expired(IDLE - 1))   # t=9 仍有效
        self.assertTrue(s.is_expired(IDLE))        # t=10 边界上已过期
        self.assertFalse(s.touch(IDLE))            # 边界续期被拒绝
        self.assertEqual(s.last_activity, 0)       # 状态未被修改

    def test_absolute_boundary_exact(self):
        """刚好卡在绝对边界：now == absolute_expiry 视为已过期。"""
        s = Session(now=0, idle_timeout=IDLE, absolute_timeout=ABSOLUTE)
        s.touch(9)
        s.touch(18)
        s.touch(25)
        self.assertFalse(s.is_expired(ABSOLUTE - 1))  # t=29 仍有效
        self.assertTrue(s.is_expired(ABSOLUTE))       # t=30 边界上已过期
        self.assertFalse(s.touch(ABSOLUTE))

    def test_renew_one_tick_before_boundary(self):
        """边界前一时刻续期成功，空闲窗口整体滑动。"""
        s = Session(now=0, idle_timeout=IDLE, absolute_timeout=ABSOLUTE)
        self.assertTrue(s.touch(IDLE - 1))           # t=9 续期成功
        self.assertEqual(s.idle_expiry, IDLE - 1 + IDLE)  # 新边界 t=19
        self.assertFalse(s.is_expired(18))
        self.assertTrue(s.is_expired(19))

    def test_simultaneous_renew_and_expire_is_deterministic(self):
        """续期与过期同一时刻：结论确定——先判过期，续期必被拒绝。"""
        s1 = Session(now=0, idle_timeout=IDLE, absolute_timeout=ABSOLUTE)
        s2 = Session(now=0, idle_timeout=IDLE, absolute_timeout=ABSOLUTE)
        self.assertFalse(s1.touch(IDLE))       # 撞空闲边界
        self.assertFalse(s2.touch(ABSOLUTE))   # 撞绝对边界


class TestClockRollback(unittest.TestCase):
    def test_rollback_does_not_extend_idle_window(self):
        """时钟回拨：last_activity 不回退，空闲窗口不被拉长。"""
        s = Session(now=100, idle_timeout=IDLE, absolute_timeout=ABSOLUTE)
        self.assertTrue(s.touch(90))           # 回拨 10s，仍判定成功（未过期）
        self.assertEqual(s.last_activity, 100)  # 起点不回退
        ri, ra, expired = s.remaining(90)
        self.assertEqual((ri, ra, expired), (20, 40, False))
        # 关键：t=110 仍然过期，回拨没有赚到额外寿命
        self.assertTrue(s.is_expired(110))

    def test_rollback_does_not_change_absolute_expiry(self):
        s = Session(now=100, idle_timeout=IDLE, absolute_timeout=ABSOLUTE)
        s.touch(50)
        self.assertEqual(s.absolute_expiry, 130)
        self.assertTrue(s.is_expired(130))


class TestStore(unittest.TestCase):
    def test_store_reports_both_remaining_times(self):
        """每种操作之后都同时给出两个剩余时间。"""
        store = SessionStore(idle_timeout=IDLE, absolute_timeout=ABSOLUTE)
        store.create("a", now=0)
        alive, ri, ra = store.remaining("a", now=3)
        self.assertEqual((alive, ri, ra), (True, 7, 27))
        ok, ri, ra = store.touch("a", now=8)
        self.assertEqual((ok, ri, ra), (True, 10, 22))
        alive, ri, ra = store.remaining("missing", now=8)
        self.assertEqual((alive, ri, ra), (False, 0, 0))

    def test_sweep_removes_expired(self):
        store = SessionStore(idle_timeout=IDLE, absolute_timeout=ABSOLUTE)
        store.create("a", now=0)
        store.create("b", now=0)
        store.touch("a", now=9)
        self.assertEqual(store.sweep(now=15), 1)   # b 空闲过期，a 存活
        self.assertEqual(len(store), 1)
        self.assertIsNotNone(store.get("a"))


if __name__ == "__main__":
    unittest.main()
