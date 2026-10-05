"""RevocationCache 自测：时间由 FakeClock 注入，查询源由脚本化 fetcher 模拟。"""

import unittest

from revocation_cache import (
    ALLOW,
    REJECT,
    RevocationCache,
    RevocationSourceError,
    RevocationTimeoutError,
    Status,
    UnavailablePolicy,
)


class FakeClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class ScriptedFetcher:
    """按脚本返回状态或抛错；script 为空时返回 default。"""

    def __init__(self, script=(), default=Status.GOOD):
        self.script = list(script)
        self.default = default
        self.calls = 0

    def __call__(self, serial):
        self.calls += 1
        if self.script:
            action = self.script.pop(0)
            if isinstance(action, Exception):
                raise action
            return action
        return self.default


TTL = 900.0
STALE = 300.0


def make_cache(fetcher, policy=UnavailablePolicy.DENY, clock=None):
    clock = clock or FakeClock()
    return RevocationCache(fetcher, ttl=TTL, stale_window=STALE,
                           policy=policy, clock=clock), clock


class TestCacheHit(unittest.TestCase):
    def test_hit_within_ttl_no_refetch(self):
        f = ScriptedFetcher()
        c, clk = make_cache(f)
        v1 = c.check("A")
        clk.advance(TTL - 1)          # 仍在有效期内
        v2 = c.check("A")
        self.assertTrue(v1.allowed and v2.allowed)
        self.assertEqual(v1.source, "live")
        self.assertEqual(v2.source, "cache")
        self.assertEqual(f.calls, 1)  # 命中不查询

    def test_boundary_expiry(self):
        # 过期判定：now < expires_at 为 fresh；now == expires_at 即过期。
        f = ScriptedFetcher()
        c, clk = make_cache(f)
        c.check("A")                  # t0 取到 GOOD，expires_at = t0+TTL
        clk.advance(TTL)              # now == expires_at，恰好过期
        v = c.check("A")
        self.assertEqual(f.calls, 2)  # 触发刷新
        self.assertEqual(v.source, "live")
        self.assertTrue(v.allowed)


class TestUnavailable(unittest.TestCase):
    def test_timeout_default_deny(self):
        f = ScriptedFetcher(script=[RevocationTimeoutError("boom")])
        c, _ = make_cache(f)
        v = c.check("A")
        self.assertEqual(v.decision, REJECT)
        self.assertEqual(v.status, Status.UNKNOWN)
        self.assertEqual(v.source, "no-data")

    def test_error_default_deny(self):
        f = ScriptedFetcher(script=[RevocationSourceError("500")])
        c, _ = make_cache(f)
        self.assertEqual(c.check("A").decision, REJECT)

    def test_upstream_unknown_default_deny(self):
        f = ScriptedFetcher(default=Status.UNKNOWN)
        c, _ = make_cache(f)
        v = c.check("A")
        self.assertEqual(v.decision, REJECT)
        self.assertEqual(v.status, Status.UNKNOWN)

    def test_fail_open_policy(self):
        f = ScriptedFetcher(script=[RevocationTimeoutError()])
        c, _ = make_cache(f, policy=UnavailablePolicy.ALLOW)
        v = c.check("A")
        self.assertEqual(v.decision, ALLOW)
        self.assertEqual(v.status, Status.UNKNOWN)  # 放行但不担保有效
        self.assertEqual(v.source, "no-data")

    def test_degraded_serves_stale_within_window(self):
        f = ScriptedFetcher(script=[Status.GOOD, RevocationTimeoutError()])
        c, clk = make_cache(f, policy=UnavailablePolicy.DEGRADED)
        c.check("A")                  # 缓存 GOOD
        clk.advance(TTL + STALE - 1)  # 过期但在 stale 窗口内
        v = c.check("A")
        self.assertEqual(v.decision, ALLOW)
        self.assertEqual(v.source, "stale")

    def test_degraded_denies_beyond_window(self):
        f = ScriptedFetcher(script=[Status.GOOD, RevocationTimeoutError()])
        c, clk = make_cache(f, policy=UnavailablePolicy.DEGRADED)
        c.check("A")
        clk.advance(TTL + STALE)      # 超出 stale 窗口（边界即不可用）
        v = c.check("A")
        self.assertEqual(v.decision, REJECT)
        self.assertEqual(v.status, Status.UNKNOWN)

    def test_expired_refresh_timeout_default_deny(self):
        f = ScriptedFetcher(script=[Status.GOOD, RevocationTimeoutError()])
        c, clk = make_cache(f)        # 默认 DENY
        c.check("A")
        clk.advance(TTL)              # 过期，刷新超时
        v = c.check("A")
        self.assertEqual(v.decision, REJECT)
        self.assertEqual(v.status, Status.UNKNOWN)


class TestRevokedSticky(unittest.TestCase):
    def test_revoked_never_reverts_on_refresh(self):
        # 吊销 -> 上游改口 GOOD -> 仍拒绝（粘性）
        f = ScriptedFetcher(script=[Status.REVOKED, Status.GOOD])
        c, clk = make_cache(f)
        v1 = c.check("A")
        self.assertEqual(v1.decision, REJECT)
        self.assertEqual(v1.status, Status.REVOKED)
        clk.advance(TTL)              # 过期，刷新拿到 GOOD
        v2 = c.check("A")
        self.assertEqual(v2.decision, REJECT)
        self.assertEqual(v2.status, Status.REVOKED)
        self.assertEqual(f.calls, 2)

    def test_revoked_sticky_survives_outage(self):
        # 吊销 -> 查询超时 -> 仍拒绝
        f = ScriptedFetcher(script=[Status.REVOKED, RevocationTimeoutError()])
        c, clk = make_cache(f)
        c.check("A")
        clk.advance(TTL)
        v = c.check("A")
        self.assertEqual(v.decision, REJECT)
        self.assertEqual(v.status, Status.REVOKED)

    def test_good_to_revoked_on_refresh(self):
        # GOOD 过期后刷新为 REVOKED -> 拒绝，且此后粘性
        f = ScriptedFetcher(script=[Status.GOOD, Status.REVOKED, Status.GOOD])
        c, clk = make_cache(f)
        self.assertTrue(c.check("A").allowed)
        clk.advance(TTL)
        v = c.check("A")
        self.assertEqual(v.decision, REJECT)
        clk.advance(TTL)              # 再次刷新，上游又说 GOOD
        self.assertEqual(c.check("A").decision, REJECT)

    def test_revoked_sticky_even_with_fail_open(self):
        # 即使 ALLOW 策略，粘性吊销也优先于放行
        f = ScriptedFetcher(script=[Status.REVOKED, RevocationTimeoutError()])
        c, clk = make_cache(f, policy=UnavailablePolicy.ALLOW)
        c.check("A")
        clk.advance(TTL)
        v = c.check("A")
        self.assertEqual(v.decision, REJECT)
        self.assertEqual(v.status, Status.REVOKED)


class TestStalenessLag(unittest.TestCase):
    def test_change_within_ttl_is_lagged(self):
        # TTL 内上游变吊销：缓存仍放行（接受的滞后），过期刷新后拒绝
        f = ScriptedFetcher(default=Status.GOOD)
        c, clk = make_cache(f)
        self.assertTrue(c.check("A").allowed)
        f.default = Status.REVOKED    # 上游状态变更
        clk.advance(TTL - 1)          # 仍在 TTL 内
        v = c.check("A")
        self.assertTrue(v.allowed)    # 滞后：仍按缓存放行
        self.assertEqual(f.calls, 1)  # 未查询
        clk.advance(1)                # 过期
        v = c.check("A")
        self.assertEqual(v.decision, REJECT)

    def test_invalidate_cuts_lag(self):
        # 推送侧 invalidate 后立即重新查询，消除滞后
        f = ScriptedFetcher(default=Status.GOOD)
        c, clk = make_cache(f)
        c.check("A")
        f.default = Status.REVOKED
        c.invalidate("A")
        v = c.check("A")
        self.assertEqual(v.decision, REJECT)
        self.assertEqual(f.calls, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
