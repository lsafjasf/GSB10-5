"""KeyRing 自测：时间线 / 轮换 / 过期 / 并发。仅标准库，时间全部注入。

轮换时间线（秒，T0 = 1_700_000_000）：

    密钥 k1: [T0,      T0+100)   老密钥
    密钥 k2: [T0+80,   T0+180)   新密钥（轮换重叠期 80s）

    t=T0-1      t=T0        t=T0+80        t=T0+100       t=T0+180
     |-----------|============|-------------|---------------|
     无密钥      k1 首次生效   k2 生效        k1 过期        k2 过期
                 (签名=k1)    (签名=k2)      (签名=k2)
                 <--- 轮换中重叠期 [T0+80, T0+100) --->
"""

import threading
import unittest

from keyring import KeyRing, NoActiveKeyError

T0 = 1_700_000_000
DATA = b"payload-to-be-signed"


class FakeClock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def make_ring(t):
    clock = FakeClock(t)
    ring = KeyRing(now_fn=clock)
    ring.add_key("k1", b"secret-old", not_before=T0, not_after=T0 + 100)
    ring.add_key("k2", b"secret-new", not_before=T0 + 80, not_after=T0 + 180)
    return ring, clock


class TimelineTest(unittest.TestCase):
    """覆盖：首次生效 / 轮换中 / 轮换刚结束 / 已过期。"""

    def test_before_first_activation(self):
        ring, _ = make_ring(T0 - 1)
        with self.assertRaises(NoActiveKeyError):
            ring.signing_key()
        self.assertIsNone(ring.get_key("k1"))

    def test_first_activation(self):
        ring, _ = make_ring(T0)
        self.assertEqual(ring.signing_key().kid, "k1")
        kid, sig = ring.sign(DATA)
        self.assertEqual(kid, "k1")
        self.assertTrue(ring.verify(kid, DATA, sig))

    def test_during_rotation_old_signature_still_verifies(self):
        # t=T0+50：旧密钥 k1 签出内容
        ring, clock = make_ring(T0 + 50)
        kid, sig = ring.sign(DATA)
        self.assertEqual(kid, "k1")
        # t=T0+90：轮换中，签名密钥已切到 k2，但 k1 签的内容必须照样验过
        clock.t = T0 + 90
        self.assertEqual(ring.signing_key().kid, "k2")
        self.assertTrue(ring.verify("k1", DATA, sig))

    def test_right_after_rotation_old_signature_still_verifies(self):
        # 轮换刚结束（k2 刚接管签名）：t = T0+80 与 T0+99
        ring, clock = make_ring(T0 + 50)
        _, sig = ring.sign(DATA)  # k1 签的
        for t in (T0 + 80, T0 + 81, T0 + 99):
            clock.t = t
            self.assertEqual(ring.signing_key().kid, "k2")
            self.assertTrue(ring.verify("k1", DATA, sig), "t=%d 应验过" % t)

    def test_expiry_boundary_before_after(self):
        ring, clock = make_ring(T0 + 50)
        _, sig = ring.sign(DATA)  # k1 签的
        clock.t = T0 + 99   # 过期前最后一秒
        self.assertTrue(ring.verify("k1", DATA, sig))
        clock.t = T0 + 100  # not_after 开区间：恰好过期
        self.assertFalse(ring.verify("k1", DATA, sig))
        self.assertIsNone(ring.get_key("k1"))
        clock.t = T0 + 101  # 过期后
        self.assertFalse(ring.verify("k1", DATA, sig))

    def test_not_before_is_inclusive_not_after_exclusive(self):
        ring, clock = make_ring(T0)
        self.assertIsNotNone(ring.get_key("k1"))      # not_before 闭
        clock.t = T0 + 100
        self.assertIsNone(ring.get_key("k1"))         # not_after 开

    def test_all_keys_expired(self):
        ring, clock = make_ring(T0 + 180)
        with self.assertRaises(NoActiveKeyError):
            ring.signing_key()
        self.assertFalse(ring.verify("k2", DATA, "00" * 32))

    def test_rejects_garbage(self):
        ring, _ = make_ring(T0 + 50)
        kid, sig = ring.sign(DATA)
        self.assertFalse(ring.verify(kid, b"tampered", sig))       # 内容被改
        self.assertFalse(ring.verify(kid, DATA, "ff" * 32))        # 签名被改
        self.assertFalse(ring.verify("no-such-kid", DATA, sig))    # 未知 kid


class ConcurrencyTest(unittest.TestCase):
    """并发轮换 + 并发取密钥/验签：不得读到写了一半的状态。"""

    def test_concurrent_rotation_and_verify(self):
        clock = FakeClock(T0 + 90)  # 冻结在重叠期
        ring = KeyRing(now_fn=clock)
        ring.add_key("k1", b"secret-old", not_before=T0, not_after=T0 + 100)
        ring.add_key("k2", b"secret-new", not_before=T0 + 80, not_after=T0 + 180)
        failures = []
        iterations = [0] * 8
        WRITES_PER_WRITER = 1000

        def writer(base):
            for i in range(WRITES_PER_WRITER):
                # 轮换进新密钥，窗口均覆盖当前时刻
                ring.add_key("w%d-%d" % (base, i), b"w-secret", T0 + 80, T0 + 180)

        def reader(idx):
            try:
                while writers_alive[0] > 0 or iterations[idx] == 0:
                    key = ring.signing_key()
                    # 读到的签名密钥必须处于一致状态：窗口覆盖当前时刻
                    if not key.valid_at(clock.t):
                        failures.append("torn key: %r" % (key,))
                        return
                    kid, sig = ring.sign(DATA)
                    if not ring.verify(kid, DATA, sig):
                        failures.append("verify failed for kid=%s" % kid)
                        return
                    iterations[idx] += 1
            except Exception as exc:  # noqa: BLE001
                failures.append("reader exception: %r" % (exc,))

        writers_alive = [2]

        def counted_writer(base):
            try:
                writer(base)
            finally:
                with alive_lock:
                    writers_alive[0] -= 1

        alive_lock = threading.Lock()
        writers = [threading.Thread(target=counted_writer, args=(b,)) for b in range(2)]
        readers = [threading.Thread(target=reader, args=(i,)) for i in range(8)]
        for t in writers + readers:
            t.start()
        for t in writers + readers:
            t.join()

        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
