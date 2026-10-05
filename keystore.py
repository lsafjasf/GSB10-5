"""密钥存储与轮换库（仅标准库）。

设计要点：
- 密钥集合采用写时复制（copy-on-write）：任何修改都会构建一个全新的
  不可变 tuple，再在锁内一次性替换 ``_keys`` 引用。读线程只在锁内
  取一次引用，随后在无锁状态下遍历，因此永远读不到"写了一半"的状态。
- 时间由调用方注入：``now`` 可传任意可调用对象（测试用 FakeClock），
  每次 sign/verify 也可显式传 ``at`` 覆盖。
- 签名格式 ``kid:base64(hmac_sha256(secret, payload))``，验签时先按
  kid 找密钥并检查有效期，再用 compare_digest 做常数时间比较。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple


class KeyStoreError(Exception):
    """密钥库基础异常。"""


class KeyNotFoundError(KeyStoreError):
    """签名引用的 kid 不存在。"""


class KeyExpiredError(KeyStoreError):
    """密钥已过期（或尚未生效），拒绝验签。"""


class SignatureMismatchError(KeyStoreError):
    """摘要不匹配（内容被篡改或密钥不对）。"""


class MalformedSignatureError(KeyStoreError):
    """签名串格式非法。"""


@dataclass(frozen=True)
class Key:
    """一把密钥及其生效区间 [not_before, not_after)。

    区间语义：not_before <= t < not_after 时可用于签名与验签；
    t >= not_after 视为已过期，必须拒绝。
    """

    kid: str
    secret: bytes
    not_before: int
    not_after: int

    def __post_init__(self) -> None:
        if not self.kid:
            raise ValueError("kid 不能为空")
        if self.not_before >= self.not_after:
            raise ValueError("not_before 必须小于 not_after")

    def valid_at(self, t: int) -> bool:
        return self.not_before <= t < self.not_after


class KeyStore:
    """线程安全的密钥库。时间通过 now 注入，默认 time.time。"""

    def __init__(self, keys=(), now: Callable[[], float] = time.time):
        self._now = now
        self._lock = threading.Lock()
        self._keys: Tuple[Key, ...] = ()
        for key in keys:
            self.add_key(key)

    # ---- 写操作（锁内构建新快照并原子替换） ----

    def add_key(self, key: Key) -> None:
        with self._lock:
            if any(k.kid == key.kid for k in self._keys):
                raise ValueError(f"重复的 kid: {key.kid}")
            self._keys = self._keys + (key,)

    def replace_keys(self, keys) -> None:
        """整体替换密钥集合（先完整校验，再一次原子替换）。"""
        new_keys = tuple(keys)
        kids = [k.kid for k in new_keys]
        if len(set(kids)) != len(kids):
            raise ValueError("存在重复的 kid")
        with self._lock:
            self._keys = new_keys

    def rotate(self, new_key: Key, retire_before: Optional[int] = None) -> None:
        """轮换：加入新密钥，同时丢弃 not_after <= retire_before 的旧密钥。

        整个操作在一次锁持有内完成，对读者表现为单个原子动作。
        """
        with self._lock:
            if any(k.kid == new_key.kid for k in self._keys):
                raise ValueError(f"重复的 kid: {new_key.kid}")
            kept = tuple(
                k for k in self._keys
                if retire_before is None or k.not_after > retire_before
            )
            self._keys = kept + (new_key,)

    # ---- 读操作（锁内取快照引用，锁外遍历） ----

    def snapshot(self) -> Tuple[Key, ...]:
        with self._lock:
            return self._keys

    def get_key(self, kid: str) -> Optional[Key]:
        for key in self.snapshot():
            if key.kid == kid:
                return key
        return None

    def get_signing_key(self, at: Optional[int] = None) -> Key:
        """返回当前可用于签名的密钥：取 not_before 最大的有效密钥。"""
        t = self._resolve_time(at)
        valid = [k for k in self.snapshot() if k.valid_at(t)]
        if not valid:
            raise KeyNotFoundError(f"t={t} 时没有可用签名密钥")
        return max(valid, key=lambda k: k.not_before)

    # ---- 签名 / 验签 ----

    def sign(self, payload: bytes, at: Optional[int] = None) -> str:
        key = self.get_signing_key(at)
        tag = hmac.new(key.secret, payload, hashlib.sha256).digest()
        return f"{key.kid}:{base64.b64encode(tag).decode('ascii')}"

    def verify(self, payload: bytes, signature: str, at: Optional[int] = None) -> bool:
        try:
            self.verify_strict(payload, signature, at)
            return True
        except KeyStoreError:
            return False

    def verify_strict(self, payload: bytes, signature: str, at: Optional[int] = None) -> None:
        """验签，失败时抛出具体原因（供测试断言与排障）。"""
        t = self._resolve_time(at)
        kid, sep, tag_b64 = signature.partition(":")
        if not sep or not kid or not tag_b64:
            raise MalformedSignatureError(f"非法签名格式: {signature!r}")
        key = self.get_key(kid)
        if key is None:
            raise KeyNotFoundError(f"未知 kid: {kid}")
        if not key.valid_at(t):
            raise KeyExpiredError(
                f"密钥 {kid} 在 t={t} 不可用（有效期 [{key.not_before}, {key.not_after})）"
            )
        try:
            expected = base64.b64decode(tag_b64.encode("ascii"), validate=True)
        except (binascii.Error, UnicodeEncodeError) as exc:
            raise MalformedSignatureError(f"签名摘要不是合法 base64: {exc}") from exc
        actual = hmac.new(key.secret, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(actual, expected):
            raise SignatureMismatchError("摘要不匹配")

    def _resolve_time(self, at: Optional[int]) -> int:
        return int(self._now()) if at is None else at


class FakeClock:
    """测试注入用的假时钟，线程安全。"""

    def __init__(self, t: int = 0):
        self._t = t
        self._lock = threading.Lock()

    def __call__(self) -> int:
        with self._lock:
            return self._t

    def set(self, t: int) -> None:
        with self._lock:
            self._t = t

    def advance(self, delta: int) -> None:
        with self._lock:
            self._t += delta
