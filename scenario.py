"""轮换时间线与场景数据（测试与基准共用）。

时间是"注入的逻辑时间"，单位秒，取值可理解为任意 epoch 秒。
三把密钥的生效区间存在刻意安排的重叠期（overlap）：

    K1 key-v1 有效区间 [1000, 3000)
    K2 key-v2 有效区间 [2000, 4000)
    K3 key-v3 有效区间 [3000, 5000)

时间线：
    t=1000  K1 首次生效（服务开始发签名）
    t=2000  K2 下发，轮换开始；签名切换到 K2，K1 在重叠期内继续可验
    t=2800  轮换结束（所有节点已持有 K2）；签名全部来自 K2
    t=2999  K1 到期前最后一刻：轮换刚结束时发出的 K1 旧签名仍必须验过
    t=3000  K1 过期边界：K1 旧签名必须被拒；同时 K3 下发，下一轮轮换开始
    t=3999  K2 到期前最后一刻：K2 签名仍验过
    t=4000  K2 过期边界：K2 签名必须被拒
    t=5000  K3 过期：无任何有效密钥

关键保证：签名是无状态令牌，验签只看"令牌里 kid 指向的密钥在验证时刻 t
是否处于有效期 + 摘要是否匹配"。因此轮换中（t=2500）和轮换刚结束
（t=2800/2999）到达的、用旧密钥 K1 签的请求，只要 K1 尚未过期就照常验过；
一旦 t>=3000，K1 的签名一律拒绝。
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from keystore import FakeClock, Key, KeyStore

PAYLOAD = b"POST /v1/orders amount=42 nonce=7f3a9c2e"

K1 = Key("key-v1", b"secret-key-v1-0123456789abcdef00", 1000, 3000)
K2 = Key("key-v2", b"secret-key-v2-abcdef012345678900", 2000, 4000)
K3 = Key("key-v3", b"secret-key-v3-fedcba987654321000", 3000, 5000)
ALL_KEYS = (K1, K2, K3)

# (t, 事件)
TIMELINE = [
    (999, "K1 尚未生效"),
    (1000, "K1 首次生效，服务开始用 K1 发签名"),
    (2000, "K2 下发：轮换开始（K1/K2 重叠期开始）"),
    (2500, "轮换进行中：新签名用 K2，在途 K1 请求继续可验"),
    (2800, "轮换结束：全部节点已持有 K2"),
    (2999, "K1 到期前最后一刻：旧 K1 签名仍验过"),
    (3000, "K1 过期：旧 K1 签名必须拒绝；K3 下发，下一轮轮换开始"),
    (3999, "K2 到期前最后一刻：K2 签名仍验过"),
    (4000, "K2 过期：K2 签名必须拒绝"),
    (5000, "K3 过期：无有效密钥"),
]


def make_signature(key: Key, payload: bytes = PAYLOAD) -> str:
    """绕过密钥选择，直接用指定密钥生成令牌（模拟旧服务在历史时刻发的签名）。"""
    tag = hmac.new(key.secret, payload, hashlib.sha256).digest()
    return f"{key.kid}:{base64.b64encode(tag).decode('ascii')}"


class RotationTimeline:
    """随 FakeClock 推进、在边界时刻执行 rotate 的演练驱动器。"""

    def __init__(self, start: int = 999):
        self.clock = FakeClock(start)
        self.store = KeyStore([K1], now=self.clock)
        self._events_done = 0

    def goto(self, t: int) -> KeyStore:
        self.clock.set(t)
        if t >= 2000 and self._events_done < 1:
            self.store.rotate(K2)
            self._events_done = 1
        if t >= 3000 and self._events_done < 2:
            self.store.rotate(K3)
            self._events_done = 2
        return self.store
