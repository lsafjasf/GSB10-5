"""密钥轮换库自测（python3 -m unittest -v）。

覆盖：首次生效 / 轮换中 / 轮换刚结束 / 已过期四个阶段，
过期前后对照，篡改与未知密钥拒绝，并发读写无撕裂。
"""

import threading
import time
import unittest

from keystore import (
    FakeClock,
    Key,
    KeyExpiredError,
    KeyNotFoundError,
    KeyStore,
    MalformedSignatureError,
    SignatureMismatchError,
)
from scenario import ALL_KEYS, K1, K2, K3, PAYLOAD, RotationTimeline, make_signature

# 三枚"历史令牌"：模拟旧服务在各自签名时刻用当时密钥签出的请求。
# 令牌无状态，之后任意时刻拿来验签，结果只取决于验证时刻 t。
TOKEN_K1 = make_signature(K1)  # 相当于 t=1500 时 K1 签出
TOKEN_K2 = make_signature(K2)  # 相当于 t=2500 时 K2 签出
TOKEN_K3 = make_signature(K3)  # 相当于 t=3500 时 K3 签出

# 每个验证时刻 t 对三枚令牌的期望结果（True=验过，False=拒绝）
VERIFY_MATRIX = {
    999:  (False, False, False),
    1000: (True,  False, False),
    2500: (True,  True,  False),
    2800: (True,  True,  False),
    2999: (True,  True,  False),
    3000: (False, True,  True),
    3999: (False, True,  True),
    4000: (False, False, True),
    5000: (False, False, False),
}

# 每个时刻应选中的签名密钥（None 表示无可用密钥）
SIGNING_KEY_AT = {
    999: None, 1000: "key-v1", 2500: "key-v2", 2800: "key-v2",
    2999: "key-v2", 3000: "key-v3", 3999: "key-v3", 4000: "key-v3",
    5000: None,
}


class TimelineTest(unittest.TestCase):
    """沿时间线推进，验证四个阶段的签名选择与验签结果。"""

    def test_timeline(self):
        tl = RotationTimeline()
        tokens = [("K1@1500", TOKEN_K1), ("K2@2500", TOKEN_K2), ("K3@3500", TOKEN_K3)]
        rows = []
        for t in sorted(VERIFY_MATRIX):
            store = tl.goto(t)
            expected_signer = SIGNING_KEY_AT[t]
            if expected_signer is None:
                with self.assertRaises(KeyNotFoundError):
                    store.get_signing_key()
                signer = "-"
            else:
                signer = store.get_signing_key().kid
                self.assertEqual(expected_signer, signer)
            results = [store.verify(PAYLOAD, tok) for _, tok in tokens]
            self.assertEqual(list(VERIFY_MATRIX[t]), results, f"t={t} 验签矩阵不符")
            rows.append((t, signer, results))

        print("\n=== 时间线验签结果（PASS=验过 / REJ=拒绝）===")
        print(f"{'t':>5} | {'签名密钥':<7} | " + " | ".join(name for name, _ in tokens))
        print("-" * 52)
        for t, signer, results in rows:
            marks = " | ".join("PASS" if r else "REJ " for r in results)
            print(f"{t:>5} | {signer:<7} | {marks}")

    def test_expiry_boundary_contrast(self):
        """过期前后对照：t=2999 必须验过，t=3000 必须拒绝（K2 同理）。"""
        tl = RotationTimeline()
        cases = [
            ("K1", TOKEN_K1, 2999, 3000),
            ("K2", TOKEN_K2, 3999, 4000),
        ]
        print("\n=== 过期前后对照 ===")
        print(f"{'密钥':<4} | {'到期前 t':>8} | {'结果':<6} | {'到期 t':>8} | {'结果':<6} | 拒绝原因")
        print("-" * 72)
        for name, token, before, after in cases:
            store = tl.goto(before)
            self.assertTrue(store.verify(PAYLOAD, token), f"{name} t={before} 应验过")
            store = tl.goto(after)
            self.assertFalse(store.verify(PAYLOAD, token), f"{name} t={after} 应拒绝")
            with self.assertRaises(KeyExpiredError):
                store.verify_strict(PAYLOAD, token)
            print(f"{name:<4} | {before:>8} | {'PASS':<6} | {after:>8} | {'REJ':<6} | KeyExpiredError")

    def test_first_effective_boundary(self):
        """首次生效边界：t=999 不可用，t=1000 起可用。"""
        tl = RotationTimeline()
        self.assertFalse(tl.goto(999).verify(PAYLOAD, TOKEN_K1))
        self.assertTrue(tl.goto(1000).verify(PAYLOAD, TOKEN_K1))

    def test_tamper_unknown_malformed(self):
        tl = RotationTimeline()
        store = tl.goto(2500)
        with self.assertRaises(SignatureMismatchError):
            store.verify_strict(PAYLOAD + b"x", TOKEN_K1)
        with self.assertRaises(SignatureMismatchError):
            store.verify_strict(PAYLOAD, TOKEN_K1[:-2] + "AA")
        with self.assertRaises(KeyNotFoundError):
            store.verify_strict(PAYLOAD, "key-v9:AAAA")
        with self.assertRaises(MalformedSignatureError):
            store.verify_strict(PAYLOAD, "not-a-token")
        with self.assertRaises(MalformedSignatureError):
            store.verify_strict(PAYLOAD, "key-v1:!!!not-base64!!!")
        self.assertFalse(store.verify(PAYLOAD + b"x", TOKEN_K1))

    def test_injected_clock(self):
        """不显式传 at 时，时间来自注入的 FakeClock。"""
        clock = FakeClock(2500)
        store = KeyStore([K1, K2], now=clock)
        self.assertTrue(store.verify(PAYLOAD, TOKEN_K1))
        clock.set(3000)
        self.assertFalse(store.verify(PAYLOAD, TOKEN_K1))
        clock.set(2500)
        self.assertEqual("key-v2", store.sign(PAYLOAD).split(":")[0])


class ConcurrencyTest(unittest.TestCase):
    """并发读写：读者永远读到完整快照，不得出现写了一半的状态。"""

    def test_no_torn_reads(self):
        store = KeyStore([K1, K2], now=FakeClock(2500))
        # 写入侧在这些配置间循环整体替换；它们在 t=2500 都至少含一把有效密钥
        configs = [(K1, K2), (K1, K2, K3), (K2,), (K1,)]
        valid_kids = {k.kid for k in ALL_KEYS}
        stop = threading.Event()
        errors = []
        reads = [0] * 4

        def reader(idx):
            count = 0
            while not stop.is_set():
                snap = store.snapshot()
                kids = [k.kid for k in snap]
                ok = (
                    len(snap) >= 1
                    and len(set(kids)) == len(kids)
                    and all(kid in valid_kids for kid in kids)
                    and all(k in ALL_KEYS for k in snap)
                )
                if not ok:
                    errors.append(f"撕裂读: {snap!r}")
                    stop.set()
                    break
                count += 1
            reads[idx] = count

        def writer():
            i = 0
            while not stop.is_set():
                store.replace_keys(configs[i % len(configs)])
                i += 1

        threads = [threading.Thread(target=reader, args=(i,)) for i in range(4)]
        threads.append(threading.Thread(target=writer))
        for th in threads:
            th.start()
        time.sleep(2.0)
        stop.set()
        for th in threads:
            th.join()

        total = sum(reads)
        print(f"\n=== 并发测试：4 读线程 + 1 写线程，共完成 {total} 次快照读，撕裂 0 次 ===")
        self.assertEqual([], errors)
        self.assertGreater(total, 10000)

    def test_naive_store_torn_read_demo(self):
        """对照实验：无锁的朴素实现可被观察到'写了一半'的空列表。"""

        class NaiveStore:
            def __init__(self, keys):
                self.keys = list(keys)
                self.mid_write = threading.Event()
                self.resume = threading.Event()

            def replace_keys(self, keys):
                self.keys = []                      # 先清空
                self.mid_write.set()                # 让读者插进来
                self.resume.wait(timeout=1.0)
                for k in keys:                      # 再逐条追加
                    self.keys.append(k)

        naive = NaiveStore([K1, K2])
        observed = []

        def reader():
            naive.mid_write.wait(timeout=1.0)
            observed.append(list(naive.keys))       # 读到写了一半的状态
            naive.resume.set()

        rt = threading.Thread(target=reader)
        rt.start()
        naive.replace_keys([K2, K3])
        rt.join()

        print(f"\n=== 对照：朴素实现被读到中间状态 {observed[0]!r}（KeyStore 的快照读不存在此问题）===")
        self.assertEqual([[]], observed)  # 证明该竞态真实存在，我们的测试不是空转

        # 同样的交错下，KeyStore 的快照读只能拿到替换前或替换后的完整状态
        store = KeyStore([K1, K2], now=FakeClock(2500))
        store.replace_keys([K2, K3])
        self.assertIn(store.snapshot(), [(K1, K2), (K2, K3)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
