"""密钥存储与轮换库（仅标准库）。

时间语义：
- 每个密钥有生效区间 [not_before, not_after)，not_before 闭、not_after 开。
- 签名密钥：当前时刻处于生效区间内、not_before 最大的那个密钥（最新生效者优先）。
- 验签：只要该 kid 对应的密钥在当前时刻仍处于生效区间内即可验过；
  因此轮换重叠期内旧密钥签出的内容照样能验过，过期后立即拒绝。
- 时间通过 now_fn 注入，测试可完全控制时钟。
- 所有读写在同一把锁下完成，并发读取不会看到写了一半的状态。
"""

import hashlib
import hmac
import threading

__all__ = ["Key", "KeyRing", "NoActiveKeyError"]


class NoActiveKeyError(Exception):
    """当前时刻没有任何可用于签名的密钥。"""


class Key:
    __slots__ = ("kid", "secret", "not_before", "not_after")

    def __init__(self, kid, secret, not_before, not_after):
        if not_before >= not_after:
            raise ValueError("not_before must be < not_after")
        self.kid = kid
        self.secret = secret
        self.not_before = not_before
        self.not_after = not_after

    def valid_at(self, t):
        return self.not_before <= t < self.not_after

    def __repr__(self):
        return "Key(kid=%r, [%r, %r))" % (self.kid, self.not_before, self.not_after)


class KeyRing:
    def __init__(self, now_fn):
        if not callable(now_fn):
            raise TypeError("now_fn must be callable")
        self._now = now_fn
        self._lock = threading.Lock()
        self._keys = {}  # kid -> Key

    # ---- 写路径（轮换） ----

    def add_key(self, kid, secret, not_before, not_after):
        """加入/轮换密钥。若 kid 已存在则整体替换（原子生效）。"""
        key = Key(kid, secret, not_before, not_after)
        with self._lock:
            self._keys[kid] = key
        return key

    def remove_key(self, kid):
        with self._lock:
            self._keys.pop(kid, None)

    # ---- 读路径 ----

    def get_key(self, kid):
        """取密钥：仅当该密钥当前处于生效区间内才返回，否则返回 None。"""
        now = self._now()
        with self._lock:
            key = self._keys.get(kid)
        if key is not None and key.valid_at(now):
            return key
        return None

    def signing_key(self):
        """当前签名密钥：生效区间内 not_before 最大者。没有则抛 NoActiveKeyError。"""
        now = self._now()
        with self._lock:
            snapshot = list(self._keys.values())
        best = None
        for key in snapshot:
            if key.valid_at(now) and (best is None or key.not_before > best.not_before):
                best = key
        if best is None:
            raise NoActiveKeyError("no active signing key at t=%r" % (now,))
        return best

    def snapshot(self):
        """当前全部密钥的一致快照（用于测试/调试）。"""
        with self._lock:
            return dict(self._keys)

    # ---- 签名 / 验签 ----

    @staticmethod
    def _mac(secret, data):
        return hmac.new(secret, data, hashlib.sha256).hexdigest()

    def sign(self, data):
        """用当前签名密钥签名，返回 (kid, signature)。"""
        key = self.signing_key()
        return key.kid, self._mac(key.secret, data)

    def verify(self, kid, data, signature):
        """验签：密钥当前仍在有效期内且 MAC 匹配才通过。过期/未知 kid 一律拒绝。"""
        key = self.get_key(kid)
        if key is None:
            return False
        expected = self._mac(key.secret, data)
        return hmac.compare_digest(expected, signature)
