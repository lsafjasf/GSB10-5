import unittest

from session_expiry import (
    ABSOLUTE_EXPIRED,
    ACTIVE,
    IDLE_EXPIRED,
    NOT_FOUND,
    ManualClock,
    SessionManager,
)


class SessionManagerTest(unittest.TestCase):
    def test_create_returns_both_remaining_times(self) -> None:
        clock = ManualClock(100)
        manager = SessionManager(idle_timeout=10, absolute_timeout=30, clock=clock)

        snapshot = manager.create("s")

        self.assertTrue(snapshot.valid)
        self.assertEqual(snapshot.reason, ACTIVE)
        self.assertEqual(snapshot.idle_remaining, 10)
        self.assertEqual(snapshot.absolute_remaining, 30)
        self.assertEqual(snapshot.idle_expires_at, 110)
        self.assertEqual(snapshot.absolute_expires_at, 130)

    def test_get_does_not_renew_idle_timer(self) -> None:
        clock = ManualClock()
        manager = SessionManager(idle_timeout=10, absolute_timeout=30, clock=clock)
        manager.create("s")

        clock.advance(5)
        first = manager.get("s")
        clock.advance(5)
        second = manager.touch("s")

        self.assertEqual((first.idle_remaining, first.absolute_remaining), (5, 25))
        self.assertFalse(second.valid)
        self.assertEqual(second.reason, IDLE_EXPIRED)
        self.assertEqual((second.idle_remaining, second.absolute_remaining), (0, 20))

    def test_successful_touch_renews_only_idle_timer(self) -> None:
        clock = ManualClock()
        manager = SessionManager(idle_timeout=10, absolute_timeout=30, clock=clock)
        manager.create("s")

        clock.advance(9)
        snapshot = manager.touch("s")

        self.assertTrue(snapshot.valid)
        self.assertEqual(snapshot.reason, ACTIVE)
        self.assertEqual(snapshot.idle_remaining, 10)
        self.assertEqual(snapshot.absolute_remaining, 21)
        self.assertEqual(snapshot.idle_expires_at, 19)
        self.assertEqual(snapshot.absolute_expires_at, 30)

    def test_continuous_activity_still_expires_at_absolute_deadline(self) -> None:
        clock = ManualClock()
        manager = SessionManager(idle_timeout=10, absolute_timeout=30, clock=clock)
        manager.create("s")

        for tick in (9, 18, 27):
            clock.set(tick)
            snapshot = manager.touch("s")
            self.assertTrue(snapshot.valid)
            self.assertEqual(snapshot.idle_remaining, 10)
            self.assertEqual(snapshot.absolute_remaining, 30 - tick)

        clock.set(30.5)
        snapshot = manager.touch("s")

        self.assertFalse(snapshot.valid)
        self.assertEqual(snapshot.reason, ABSOLUTE_EXPIRED)
        self.assertEqual(snapshot.idle_remaining, 6.5)
        self.assertEqual(snapshot.absolute_remaining, 0)

    def test_long_idle_expires_and_failed_touch_does_not_renew(self) -> None:
        clock = ManualClock()
        manager = SessionManager(idle_timeout=10, absolute_timeout=30, clock=clock)
        manager.create("s")

        clock.advance(11)
        first = manager.touch("s")
        second = manager.get("s")

        self.assertFalse(first.valid)
        self.assertEqual(first.reason, IDLE_EXPIRED)
        self.assertEqual((first.idle_remaining, first.absolute_remaining), (0, 19))
        self.assertEqual(second.idle_expires_at, 10)
        self.assertEqual((second.idle_remaining, second.absolute_remaining), (0, 19))

    def test_touch_at_idle_boundary_is_expired(self) -> None:
        clock = ManualClock()
        manager = SessionManager(idle_timeout=10, absolute_timeout=30, clock=clock)
        manager.create("s")

        clock.advance(10)
        snapshot = manager.touch("s")

        self.assertFalse(snapshot.valid)
        self.assertEqual(snapshot.reason, IDLE_EXPIRED)
        self.assertEqual((snapshot.idle_remaining, snapshot.absolute_remaining), (0, 20))
        self.assertEqual(snapshot.idle_expires_at, 10)

    def test_touch_at_absolute_boundary_is_expired(self) -> None:
        clock = ManualClock()
        manager = SessionManager(idle_timeout=100, absolute_timeout=50, clock=clock)
        manager.create("s")

        clock.advance(50)
        snapshot = manager.touch("s")

        self.assertFalse(snapshot.valid)
        self.assertEqual(snapshot.reason, ABSOLUTE_EXPIRED)
        self.assertEqual((snapshot.idle_remaining, snapshot.absolute_remaining), (50, 0))
        self.assertEqual(snapshot.absolute_expires_at, 50)

    def test_absolute_reason_wins_when_both_boundaries_coincide(self) -> None:
        clock = ManualClock()
        manager = SessionManager(idle_timeout=10, absolute_timeout=10, clock=clock)
        manager.create("s")

        clock.advance(10)
        snapshot = manager.touch("s")

        self.assertFalse(snapshot.valid)
        self.assertEqual(snapshot.reason, ABSOLUTE_EXPIRED)
        self.assertEqual((snapshot.idle_remaining, snapshot.absolute_remaining), (0, 0))

    def test_clock_rollback_does_not_increase_remaining_time(self) -> None:
        clock = ManualClock(100)
        manager = SessionManager(idle_timeout=10, absolute_timeout=30, clock=clock)
        manager.create("s")

        clock.advance(9)
        before = manager.touch("s")
        clock.set(105)
        after = manager.get("s")

        self.assertEqual((before.idle_remaining, before.absolute_remaining), (10, 21))
        self.assertEqual((after.idle_remaining, after.absolute_remaining), (10, 21))

        clock.set(119)
        boundary = manager.get("s")
        self.assertFalse(boundary.valid)
        self.assertEqual(boundary.reason, IDLE_EXPIRED)
        self.assertEqual((boundary.idle_remaining, boundary.absolute_remaining), (0, 11))

    def test_missing_session_reports_zero_for_both_remaining_times(self) -> None:
        manager = SessionManager(10, 30, clock=ManualClock())

        snapshot = manager.get("missing")

        self.assertFalse(snapshot.valid)
        self.assertEqual(snapshot.reason, NOT_FOUND)
        self.assertEqual(snapshot.idle_remaining, 0)
        self.assertEqual(snapshot.absolute_remaining, 0)

    def test_purge_expired_removes_only_expired_sessions(self) -> None:
        clock = ManualClock()
        manager = SessionManager(idle_timeout=10, absolute_timeout=30, clock=clock)
        manager.create("old")
        clock.advance(11)
        manager.create("new")

        removed = manager.purge_expired()

        self.assertEqual(removed, 1)
        self.assertEqual(manager.get("old").reason, NOT_FOUND)
        self.assertTrue(manager.get("new").valid)

    def test_invalid_configuration_is_rejected(self) -> None:
        for idle_timeout, absolute_timeout in (
            (0, 10),
            (-1, 10),
            (10, 0),
            (10, -1),
            (float("inf"), 10),
            (10, float("nan")),
        ):
            with self.subTest(idle_timeout=idle_timeout, absolute_timeout=absolute_timeout):
                with self.assertRaises(ValueError):
                    SessionManager(idle_timeout, absolute_timeout, clock=ManualClock())


if __name__ == "__main__":
    unittest.main()
