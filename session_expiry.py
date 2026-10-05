"""会话过期与续期库（仅标准库，时间由调用方注入，不使用系统时钟）。

计时规则
========
两套计时相互独立，各自有明确的起点与更新方式：

1. 空闲过期（idle，滑动计时）
   - 起点：会话创建时刻 created_at。
   - 更新：每次续期（touch）成功时，把起点滑动到本次操作时刻 now，
     即 idle_expiry = last_activity + idle_timeout。
   - 时钟回拨保护：若 touch 的 now 早于 last_activity，则忽略本次回拨，
     last_activity 不回退（空闲窗口不会因时钟回拨而被拉长）。

2. 绝对过期（absolute，固定计时）
   - 起点：会话创建时刻 created_at。
   - 更新：永不更新。absolute_expiry = created_at + absolute_timeout，
     任何续期、任何操作都不会改变它。因此"只要续期就永不过期"不成立。

边界判定（确定性结论）
======================
- 过期判定采用闭区间：remaining <= 0 即过期。
  也就是 now == expiry 这一时刻，会话已经过期（剩余时间为 0）。
- 续期与过期发生在同一时刻：先判过期再续期。
  在 now == idle_expiry 或 now == absolute_expiry 时刻调用 touch，
  会话被判定为已过期，续期被拒绝（返回 False）。结论确定，无歧义。
- 会话有效 <=> 剩余空闲时间 > 0 且 剩余绝对时间 > 0（两者都要报）。
"""


class Session:
    """单个会话。所有时间均为调用方注入的数值（秒，可为 int/float）。"""

    __slots__ = ("created_at", "last_activity", "idle_timeout", "absolute_timeout")

    def __init__(self, now, idle_timeout, absolute_timeout):
        if idle_timeout <= 0:
            raise ValueError("idle_timeout 必须为正数")
        if absolute_timeout <= 0:
            raise ValueError("absolute_timeout 必须为正数")
        self.created_at = now
        self.last_activity = now
        self.idle_timeout = idle_timeout
        self.absolute_timeout = absolute_timeout

    @property
    def idle_expiry(self):
        """空闲过期时刻 = 最近一次活动 + 空闲超时时长（滑动）。"""
        return self.last_activity + self.idle_timeout

    @property
    def absolute_expiry(self):
        """绝对过期时刻 = 创建时刻 + 绝对寿命（固定，永不滑动）。"""
        return self.created_at + self.absolute_timeout

    def remaining(self, now):
        """返回 (剩余空闲时间, 剩余绝对时间, 是否已过期)。

        两个剩余时间必须同时给出；任一 <= 0 即判定过期。
        """
        remaining_idle = self.idle_expiry - now
        remaining_absolute = self.absolute_expiry - now
        expired = remaining_idle <= 0 or remaining_absolute <= 0
        return remaining_idle, remaining_absolute, expired

    def is_expired(self, now):
        return self.remaining(now)[2]

    def touch(self, now):
        """续期：把空闲计时的起点滑动到 now。

        返回 True 表示续期成功；会话已过期（含恰好在过期时刻）则返回 False，
        且不做任何修改。时钟回拨（now < last_activity）时不回退起点。
        """
        if self.is_expired(now):
            return False
        if now > self.last_activity:
            self.last_activity = now
        return True


class SessionStore:
    """会话集合：创建 / 续期 / 查询 / 清理。"""

    __slots__ = ("idle_timeout", "absolute_timeout", "_sessions")

    def __init__(self, idle_timeout, absolute_timeout):
        self.idle_timeout = idle_timeout
        self.absolute_timeout = absolute_timeout
        self._sessions = {}

    def create(self, sid, now):
        session = Session(now, self.idle_timeout, self.absolute_timeout)
        self._sessions[sid] = session
        return session

    def get(self, sid):
        return self._sessions.get(sid)

    def touch(self, sid, now):
        """续期。返回 (是否成功, 剩余空闲时间, 剩余绝对时间)。"""
        session = self._sessions.get(sid)
        if session is None:
            return False, 0, 0
        ok = session.touch(now)
        remaining_idle, remaining_absolute, _ = session.remaining(now)
        return ok, max(remaining_idle, 0), max(remaining_absolute, 0)

    def remaining(self, sid, now):
        """查询。返回 (是否存在且未过期, 剩余空闲时间, 剩余绝对时间)。"""
        session = self._sessions.get(sid)
        if session is None:
            return False, 0, 0
        remaining_idle, remaining_absolute, expired = session.remaining(now)
        return (not expired), max(remaining_idle, 0), max(remaining_absolute, 0)

    def sweep(self, now):
        """清理已过期会话，返回清理数量。"""
        expired_sids = [
            sid for sid, s in self._sessions.items() if s.is_expired(now)
        ]
        for sid in expired_sids:
            del self._sessions[sid]
        return len(expired_sids)

    def __len__(self):
        return len(self._sessions)
