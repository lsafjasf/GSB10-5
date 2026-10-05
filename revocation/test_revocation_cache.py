"""RevocationCache 自测：时间全部由 FakeClock 注入，不依赖真实时钟。"""

import unittest

from revocation_cache import (
    FailMode,
    QueryError,
    QueryTimeout,
    RevocationCache,
    Status,
)


class FakeClock:
    def __init__(self, t: float = 1_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class FakeQuerier:
    """按脚本返回结果；可设置抛错。记录调用次数。"""

    def __init__(self):
        self.calls = 0
        self.status = Status.GOOD
        self.next_update = None
        self.error = None

    def query(self, serial):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.status, self.next_update


def make_cache(querier, clock, fail_mode=FailMode.FAIL_CLOSED, ttl=300.0, max_stale=3600.0):
    return RevocationCache(querier, ttl=ttl, fail_mode=fail_mode,
                           max_stale=max_stale, now=clock)


class TestCaching(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.querier = FakeQuerier()

    def test_cache_hit_no_second_fetch(self):
        cache = make_cache(self.querier, self.clock)
        d1 = cache.check("A")
        self.clock.advance(100)  # ttl=300 内
        d2 = cache.check("A")
        self.assertTrue(d1.allowed)
        self.assertTrue(d2.allowed)
        self.assertEqual(d2.source, "cache")
        self.assertEqual(self.querier.calls, 1)
        self.assertEqual(cache.hits, 1)

    def test_expiry_boundary_is_expired(self):
        cache = make_cache(self.querier, self.clock)
        cache.check("A")                       # t=1000, expires_at=1300
        self.clock.advance(299.9)
        cache.check("A")
        self.assertEqual(self.querier.calls, 1)  # 仍未过期
        self.clock.advance(0.1)                  # t=1300 == expires_at
        d = cache.check("A")
        self.assertEqual(self.querier.calls, 2)  # 边界视为过期，重新拉取
        self.assertEqual(d.source, "fresh")

    def test_next_update_shortens_ttl(self):
        self.querier.next_update = 1100.0        # 上游说 100s 后失效，早于 ttl=300
        cache = make_cache(self.querier, self.clock)
        cache.check("A")
        self.clock.advance(150)                  # ttl 内但已过 next_update
        cache.check("A")
        self.assertEqual(self.querier.calls, 2)


class TestFailurePolicies(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.querier = FakeQuerier()

    def test_fail_closed_no_cache_denies(self):
        self.querier.error = QueryTimeout("timeout")
        cache = make_cache(self.querier, self.clock, FailMode.FAIL_CLOSED)
        d = cache.check("A")
        self.assertFalse(d.allowed)
        self.assertEqual(d.source, "fail_closed")

    def test_fail_closed_with_stale_cache_still_denies(self):
        cache = make_cache(self.querier, self.clock, FailMode.FAIL_CLOSED)
        cache.check("A")                       # 缓存 GOOD
        self.clock.advance(301)                # 过期
        self.querier.error = QueryError("boom")
        d = cache.check("A")
        self.assertFalse(d.allowed)

    def test_fail_open_allows_on_timeout(self):
        self.querier.error = QueryTimeout("timeout")
        cache = make_cache(self.querier, self.clock, FailMode.FAIL_OPEN)
        d = cache.check("A")
        self.assertTrue(d.allowed)
        self.assertEqual(d.source, "fail_open")

    def test_fail_degraded_serves_stale_within_window(self):
        cache = make_cache(self.querier, self.clock, FailMode.FAIL_DEGRADED, max_stale=600)
        cache.check("A")                       # 缓存 GOOD, expires=1300
        self.clock.advance(400)                # 过期 100s，窗口内
        self.querier.error = QueryTimeout("timeout")
        d = cache.check("A")
        self.assertTrue(d.allowed)
        self.assertEqual(d.source, "stale")
        self.assertEqual(cache.stale_serves, 1)

    def test_fail_degraded_beyond_window_denies(self):
        cache = make_cache(self.querier, self.clock, FailMode.FAIL_DEGRADED, max_stale=600)
        cache.check("A")
        self.clock.advance(301 + 600)          # 过期 601s，超出窗口
        self.querier.error = QueryTimeout("timeout")
        d = cache.check("A")
        self.assertFalse(d.allowed)

    def test_fail_degraded_no_cache_denies(self):
        self.querier.error = QueryTimeout("timeout")
        cache = make_cache(self.querier, self.clock, FailMode.FAIL_DEGRADED)
        d = cache.check("A")
        self.assertFalse(d.allowed)


class TestRevocationMonotonicity(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.querier = FakeQuerier()

    def test_revoked_never_flips_back(self):
        self.querier.status = Status.REVOKED
        cache = make_cache(self.querier, self.clock)
        d1 = cache.check("A")
        self.assertFalse(d1.allowed)
        self.assertTrue(cache.is_pinned("A"))

        # 上游随后（错误地）声称 GOOD，且缓存早已过期
        self.querier.status = Status.GOOD
        self.clock.advance(10_000)
        calls_before = self.querier.calls
        d2 = cache.check("A")
        self.assertFalse(d2.allowed)                       # 仍然拒绝
        self.assertEqual(d2.status, Status.REVOKED)
        self.assertEqual(d2.source, "pinned")
        self.assertEqual(self.querier.calls, calls_before)  # 不再访问上游

    def test_revoked_pin_overrides_fail_open(self):
        self.querier.status = Status.REVOKED
        cache = make_cache(self.querier, self.clock, FailMode.FAIL_OPEN)
        cache.check("A")
        self.querier.error = QueryTimeout("timeout")
        self.clock.advance(10_000)
        d = cache.check("A")
        self.assertFalse(d.allowed)            # fail-open 也不能放行已吊销证书

    def test_revoked_persists_across_eviction(self):
        self.querier.status = Status.REVOKED
        cache = make_cache(self.querier, self.clock)
        cache.check("A")
        self.clock.advance(10_000)
        cache.evict_expired()
        self.assertTrue(cache.is_pinned("A"))
        self.assertFalse(cache.check("A").allowed)


class TestStatusChangeLag(unittest.TestCase):
    def test_good_to_revoked_lag_within_ttl(self):
        clock = FakeClock()
        querier = FakeQuerier()
        cache = make_cache(querier, clock, FailMode.FAIL_CLOSED, ttl=300)
        d1 = cache.check("A")                  # t=1000 缓存 GOOD
        self.assertTrue(d1.allowed)

        querier.status = Status.REVOKED        # 上游状态变为吊销
        clock.advance(100)                     # 仍在 TTL 内
        d2 = cache.check("A")
        self.assertTrue(d2.allowed)            # 滞后窗口内仍放行（已文档化接受）
        self.assertEqual(d2.source, "cache")

        clock.advance(201)                     # 过期后刷新
        d3 = cache.check("A")
        self.assertFalse(d3.allowed)           # 现在拿到 REVOKED，拒绝
        self.assertEqual(d3.status, Status.REVOKED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
