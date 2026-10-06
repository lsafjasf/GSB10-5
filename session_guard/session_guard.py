"""Session Fixation 防护库（仅标准库，Python 3.8+）。

核心规则（详见 README.md）：
1. 登录入口可以是匿名/预登录会话标识（attacker 可能预设的那个）。
2. 只有在“认证成功之后”才更换标识；认证失败时标识保持不变（确定行为）。
3. 更换是原子操作：在同一把全局锁内删除旧标识、生成新标识、迁移会话数据。
   因此旧标识一旦离开 login() 就永远失效，不存在“新旧并存”的窗口。
4. 新标识 = secrets.token_hex(32)，256 bit，来自 os.urandom（getrandom 系统调用，
   内核 CSPRNG），不可预测。
5. 并发登录同一旧标识时，全局锁决定的第一个成功者完成更换；其余请求看到的
   旧标识已被删除，得到 InvalidSessionError——结果确定（恰好 1 个成功）。
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional


class SessionError(Exception):
    """会话模块基础异常。"""


class InvalidSessionError(SessionError):
    """标识不存在或已在登录更换时被作废。"""


class AuthenticationError(SessionError):
    """凭证校验失败；此时会话标识保持不变。"""


Authenticator = Callable[[str, str], bool]


@dataclass
class Session:
    sid: str
    data: Dict[str, Any] = field(default_factory=dict)
    authenticated: bool = False
    username: Optional[str] = None
    failed_attempts: int = 0
    generation: int = 0
    created_at: float = field(default_factory=time.time)
    rotated_at: Optional[float] = None
    prev_sid_fingerprint: Optional[str] = None  # 仅留旧标识的 SHA-256 指纹供审计


def _fingerprint(sid: str) -> str:
    return hashlib.sha256(sid.encode("ascii")).hexdigest()[:16]


class SessionStore:
    """线程安全的会话存储与登录标识更换器。"""

    def __init__(self, sid_bytes: int = 32) -> None:
        if sid_bytes < 16:
            raise ValueError("sid_bytes 必须 >= 16（至少 128 bit 熵）")
        self._sid_bytes = sid_bytes
        self._sessions: Dict[str, Session] = {}
        self._lock = threading.Lock()

    # ---------- 标识生成 ----------
    def _new_sid_locked(self) -> str:
        # secrets 模块使用 os.urandom，即内核 CSPRNG（Linux 为 getrandom()）。
        while True:
            sid = secrets.token_hex(self._sid_bytes)
            if sid not in self._sessions:
                return sid

    # ---------- 生命周期 ----------
    def create_session(self, **initial_data: Any) -> str:
        """建立预登录（匿名）会话。登录前攻击者预设的标识也走这条路。"""
        with self._lock:
            sid = self._new_sid_locked()
            self._sessions[sid] = Session(sid=sid, data=dict(initial_data))
            return sid

    def get(self, sid: str) -> Session:
        with self._lock:
            session = self._sessions.get(sid)
            if session is None:
                raise InvalidSessionError("会话标识无效或已被作废")
            return session

    def login(
        self, sid: str, username: str, password: str, authenticator: Authenticator
    ) -> str:
        """在给定会话上执行登录。

        成功 -> 原子更换标识，返回新标识；旧标识立即永久失效。
        失败 -> 抛出 AuthenticationError，标识与原会话完全不变，
                failed_attempts + 1（允许在同一标识上重试）。
        旧标识已失效 -> InvalidSessionError（并发落败者与重放旧标识均如此）。
        """
        with self._lock:
            session = self._sessions.get(sid)
            if session is None:
                raise InvalidSessionError("会话标识无效或已被作废")

            # 凭证校验在锁内完成，保证“校验+更换”整体原子、并发结果确定。
            if not authenticator(username, password):
                session.failed_attempts += 1
                raise AuthenticationError("用户名或密码错误（标识保持不变）")

            new_sid = self._new_sid_locked()
            fingerprint = _fingerprint(sid)

            del self._sessions[sid]  # 旧标识先删除，绝不复用、绝不新旧并存
            session.sid = new_sid
            session.authenticated = True
            session.username = username
            session.generation += 1
            session.rotated_at = time.time()
            session.prev_sid_fingerprint = fingerprint
            self._sessions[new_sid] = session
            return new_sid

    def logout(self, sid: str) -> None:
        with self._lock:
            self._sessions.pop(sid, None)

    def active_count(self) -> int:
        with self._lock:
            return len(self._sessions)
