"""procguard — 进程信号转发与清理库（仅 Python 3 标准库）。

设计要点（详见 README.md）：
- 子进程以独立会话/进程组启动（start_new_session=True => setsid）。
- 信号转发范围：子进程所在的整个进程组（os.killpg）。
- 清理范围：进程组 + 会话级兜底清扫（捕获 setpgid 逃逸者）。
- 时间（clock/sleep）全部可注入，测试可用假时钟。
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass, field

DEFAULT_FORWARD_SIGNALS = (
    signal.SIGTERM,
    signal.SIGINT,
    signal.SIGHUP,
    signal.SIGQUIT,
)


@dataclass
class ProcInfo:
    """存活进程清单中的一项。"""
    pid: int
    pgid: int
    sid: int
    comm: str

    def __str__(self) -> str:  # 便于直接打印清单
        return f"pid={self.pid} pgid={self.pgid} sid={self.sid} comm={self.comm}"


@dataclass
class CleanupReport:
    """一次清理的结果与校验数据。"""
    members_before: list = field(default_factory=list)   # 清理前存活清单 [ProcInfo]
    term_sent_to: list = field(default_factory=list)     # 收到温和信号的 pid
    kill_sent_to: list = field(default_factory=list)     # 被强制 KILL 的 pid
    escalated: bool = False                              # 是否触发了强制兜底
    survivors: list = field(default_factory=list)        # 清理后仍存活的清单（应为空）
    signals_during_cleanup: list = field(default_factory=list)  # 清理期间再次收到的信号
    started_at: float = 0.0
    finished_at: float = 0.0

    @property
    def duration(self) -> float:
        return self.finished_at - self.started_at


# ---------------------------------------------------------------------------
# 存活进程枚举（校验数据来源）
# ---------------------------------------------------------------------------

def _list_members_procfs(sid: int) -> list:
    """Linux: 扫描 /proc，按会话 ID 过滤；排除僵尸进程（已死，待父进程收割）。"""
    out = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as fh:
                data = fh.read()
            rparen = data.rindex(b")")
            fields = data[rparen + 2:].split()
            state = fields[0]
            pgrp = int(fields[2])
            sess = int(fields[3])
            if state == b"Z":
                continue
            if sess == sid:
                comm = data[data.index(b"(") + 1:rparen].decode(errors="replace")
                out.append(ProcInfo(int(name), pgrp, sess, comm))
        except (FileNotFoundError, ProcessLookupError, ValueError, IndexError, PermissionError):
            continue
    return out


def _list_members_ps(sid: int) -> list:
    """非 Linux 兜底：解析 ps 输出。"""
    out = []
    try:
        res = subprocess.run(
            ["ps", "-eo", "pid=,pgid=,sid=,stat=,comm="],
            capture_output=True, text=True, check=False,
        )
    except OSError:
        return out
    for line in res.stdout.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        try:
            pid, pgid, sess = int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            continue
        if parts[3].startswith("Z"):
            continue
        if sess == sid:
            out.append(ProcInfo(pid, pgid, sess, parts[4]))
    return out


def list_session_members(sid: int) -> list:
    """返回指定会话中全部存活（非僵尸）进程清单。"""
    if os.path.isdir("/proc"):
        return _list_members_procfs(sid)
    return _list_members_ps(sid)


# ---------------------------------------------------------------------------
# 监管者：负责启动、转发、清理
# ---------------------------------------------------------------------------

class Supervisor:
    """启动一个子进程（独立会话/进程组），转发信号并负责彻底清理。

    参数:
        argv:            子进程命令行。
        grace:           温和信号后的优雅退出宽限（秒），超时后升级为 SIGKILL。
        poll_interval:   等待进程组清空时的轮询间隔（秒）。
        clock / sleep:   时间源与睡眠函数，测试可注入假时钟。
        forward_signals: 需要转发到子进程进程组的信号集合。
        term_signal:     清理第一阶段发送的温和信号。
        kill_signal:     兜底强制信号（默认 SIGKILL）。
    """

    def __init__(self, argv, *, grace=5.0, poll_interval=0.01,
                 clock=time.monotonic, sleep=time.sleep,
                 forward_signals=DEFAULT_FORWARD_SIGNALS,
                 term_signal=signal.SIGTERM, kill_signal=signal.SIGKILL,
                 env=None, cwd=None):
        self.argv = argv
        self.grace = grace
        self.poll_interval = poll_interval
        self.clock = clock
        self.sleep = sleep
        self.forward_signals = tuple(forward_signals)
        self.term_signal = term_signal
        self.kill_signal = kill_signal
        self.env = env
        self.cwd = cwd

        self._proc = None
        self.sid = None
        self.pgid = None
        self._cleaning = False
        self._signals_during_cleanup = []
        self._old_handlers = {}
        self.last_report = None

    # -- 生命周期 ----------------------------------------------------------

    def start(self):
        """以新会话启动子进程，并安装信号转发处理器。"""
        if self._proc is not None:
            raise RuntimeError("already started")
        self._proc = subprocess.Popen(
            self.argv, start_new_session=True, env=self.env, cwd=self.cwd,
        )
        # setsid 后子进程既是自己进程组的组长，也是新会话的会话首进程
        self.sid = self._proc.pid
        self.pgid = self._proc.pid
        self._install_handlers()
        return self._proc

    def _install_handlers(self):
        try:
            for sig in self.forward_signals:
                self._old_handlers[sig] = signal.signal(sig, self._on_signal)
        except ValueError:
            # 非主线程无法安装信号处理器：转发关闭，terminate() 仍可用
            self._old_handlers = {}

    def restore_handlers(self):
        for sig, handler in self._old_handlers.items():
            signal.signal(sig, handler)
        self._old_handlers = {}

    # -- 信号转发 ----------------------------------------------------------

    def _on_signal(self, signum, frame):
        """收到信号：转发给子进程进程组；清理期间收到的信号只记录、不打断清理。"""
        if self._cleaning:
            self._signals_during_cleanup.append(signum)
            return
        # 所有被转发的信号都视为终止意图：先快照再向进程组发信号并清理，
        # 避免“信号先到、进程已退出”导致清单与转发目标漏掉
        self.last_report = self.terminate(term_signal=signum)

    def _forward(self, signum):
        try:
            os.killpg(self.pgid, signum)
        except (ProcessLookupError, PermissionError):
            pass

    # -- 清理 --------------------------------------------------------------

    def members(self) -> list:
        """当前会话内存活进程清单（校验数据）。"""
        if self.sid is None:
            return []
        return list_session_members(self.sid)

    def terminate(self, term_signal=None) -> CleanupReport:
        """两阶段清理：温和信号 -> 宽限等待 -> 强制兜底 -> 会话级清扫 -> 校验。

        term_signal 指定第一阶段发送的信号（信号转发时为实际收到的信号）。
        可重入安全：清理进行中再次调用（或再次收到信号）不会打断本次清理。
        """
        if self._cleaning:
            return None
        if self._proc is None:
            raise RuntimeError("not started")
        term_signal = self.term_signal if term_signal is None else term_signal
        self._cleaning = True
        report = CleanupReport(started_at=self.clock())
        try:
            # 阶段 0：先记录清理前存活清单，再发信号
            report.members_before = self.members()
            report.term_sent_to = [m.pid for m in report.members_before]

            # 阶段 1：温和信号到整个进程组（覆盖多层派生）
            self._signal_group(term_signal)
            self._reap_nonblocking()

            # 阶段 2：宽限等待；未清空则强制兜底
            if not self._wait_empty(self.grace):
                report.escalated = True
                remaining = self.members()
                report.kill_sent_to = [m.pid for m in remaining]
                self._signal_group(self.kill_signal)
                self._reap_blocking()
                # 会话级清扫：捕获在会话内调用 setpgid 脱离进程组的逃逸者
                for m in self.members():
                    self._kill_pid(m.pid)
                self._wait_empty(self.grace)
            else:
                self._reap_blocking()

            # 阶段 3：校验——清理结束后必须没有遗留进程
            report.survivors = self.members()
            report.signals_during_cleanup = list(self._signals_during_cleanup)
        finally:
            self._cleaning = False
            report.finished_at = self.clock()
        return report

    # -- 内部工具 ----------------------------------------------------------

    def _signal_group(self, sig):
        try:
            os.killpg(self.pgid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    @staticmethod
    def _kill_pid(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    def _wait_empty(self, timeout) -> bool:
        deadline = self.clock() + timeout
        while True:
            if not self.members():
                return True
            remaining = deadline - self.clock()
            if remaining <= 0:
                return False
            self.sleep(min(self.poll_interval, remaining))

    def _reap_nonblocking(self):
        if self._proc is not None:
            self._proc.poll()

    def _reap_blocking(self):
        # 此时进程组已空（或已被 KILL），wait 会立即返回，避免留下僵尸
        if self._proc is not None:
            try:
                self._proc.wait()
            except Exception:
                pass

    # -- 上下文管理 --------------------------------------------------------

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc, tb):
        self.terminate()
        self.restore_handlers()
        return False
