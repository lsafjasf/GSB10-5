"""会话固定（Session Fixation）防护库 —— 仅使用 Python 3 标准库。

核心规则
--------
1. 登录成功时必须更换会话标识（session ID rotation）：
   - 时机：在“凭证校验通过”之后、“会话被标记为已认证”之前，原子地完成换号。
   - 规则：旧标识立即从会话表中删除（失效），会话数据原样迁移到由
     CSPRNG 生成的全新标识下；新旧标识无任何派生关系。
2. 登录失败（凭证错误）：不更换标识，仅记录失败次数。攻击者预设的
   标识不会因此获得任何有效状态。
3. 并发登录：所有会话表操作在同一把互斥锁下原子完成。对同一个旧标识
   并发发起登录时，恰好只有一个请求能换号成功；其余请求因旧标识已失效
   而被拒绝（InvalidSessionError）。行为确定、可重复。
4. 新标识来源：secrets.token_hex(16) -> 128 bit，取自 os.urandom，
   即操作系统 CSPRNG（Linux getrandom(2)），不可预测。
"""

from __future__ import annotations

import secrets
import threading
import time


class SessionError(Exception):
    """会话相关错误基类。"""


class InvalidSessionError(SessionError):
    """会话标识不存在或已失效（包括登录换号后的旧标识）。"""


class AuthenticationError(SessionError):
    """凭证校验失败；会话标识保持不变。"""


class _Session:
    __slots__ = (
        "session_id",
        "user_id",
        "authenticated",
        "created_at",
        "login_count",
        "failed_attempts",
        "data",
    )

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.user_id: str | None = None
        self.authenticated = False
        self.created_at = time.time()
        self.login_count = 0
        self.failed_attempts = 0
        self.data: dict = {}


class SessionManager:
    """线程安全的会话管理器，内置会话固定防护。"""

    def __init__(self, id_bytes: int = 16) -> None:
        if id_bytes < 16:
            raise ValueError("id_bytes 至少为 16（128 bit），否则无法保证不可预测性")
        self._id_bytes = id_bytes
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.Lock()

    # ---- 标识生成 ----------------------------------------------------
    def _new_id(self) -> str:
        # secrets 模块明确承诺适用于安全场景，底层为 os.urandom（CSPRNG）。
        return secrets.token_hex(self._id_bytes)

    # ---- 会话生命周期 ------------------------------------------------
    def create_anonymous(self) -> str:
        """建立登录前的匿名会话，返回新标识。"""
        with self._lock:
            sid = self._new_id()
            self._sessions[sid] = _Session(sid)
            return sid

    def login(self, session_id: str, user_id: str, credential_ok: bool) -> str:
        """尝试登录。

        成功：原子地换号 —— 删除旧标识、会话迁移到新标识，返回新标识。
        失败：标识不变，failed_attempts + 1，抛出 AuthenticationError。
        旧标识无效：抛出 InvalidSessionError（并发登录中的败者走这里）。
        """
        with self._lock:
            sess = self._sessions.get(session_id)
            if sess is None:
                raise InvalidSessionError(f"会话标识无效或已失效: {session_id!r}")
            if not credential_ok:
                sess.failed_attempts += 1
                raise AuthenticationError("凭证校验失败，会话标识保持不变")

            new_id = self._new_id()
            # 原子换号：旧标识立即失效，绝不留窗口期。
            del self._sessions[session_id]
            sess.session_id = new_id
            sess.user_id = user_id
            sess.authenticated = True
            sess.login_count += 1
            self._sessions[new_id] = sess
            return new_id

    def validate(self, session_id: str) -> dict:
        """校验标识是否有效，返回只读快照；无效则抛 InvalidSessionError。"""
        with self._lock:
            sess = self._sessions.get(session_id)
            if sess is None:
                raise InvalidSessionError(f"会话标识无效或已失效: {session_id!r}")
            return {
                "session_id": sess.session_id,
                "user_id": sess.user_id,
                "authenticated": sess.authenticated,
                "login_count": sess.login_count,
                "failed_attempts": sess.failed_attempts,
            }

    def logout(self, session_id: str) -> None:
        """登出：标识立即失效。"""
        with self._lock:
            if self._sessions.pop(session_id, None) is None:
                raise InvalidSessionError(f"会话标识无效或已失效: {session_id!r}")

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._sessions)
