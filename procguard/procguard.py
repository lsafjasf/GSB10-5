"""procguard - 进程组信号转发与兜底清理（仅标准库, Python 3.9+）。

核心思路
--------
被监管的子进程通过 ``start_new_session=True``（等价于 setsid(2)）启动，
它成为一个新 *会话(session)* 的会话首进程，同时也是新 *进程组(process
group)* 的组长，pgid == 子进程 pid。它的所有派生子孙默认继承同一个
pgid（进程组只有在显式调用 setsid()/setpgid() 时才会改变）。因此：

* 转发信号 = ``os.killpg(pgid, signum)``，一次投递覆盖任意层数的派生进程；
* 清理 = 先对整组发 SIGTERM（温和退出），在注入时钟控制的宽限期后轮询
  /proc 校验，仍有存活者则整组 SIGKILL（强制兜底），最后回收全部僵尸
  并再次校验，保证零遗留。
"""
from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Optional, Sequence

__all__ = [
    "RealClock",
    "FakeClock",
    "ProcInfo",
    "CleanupResult",
    "ProcessTree",
    "list_pgroup",
    "pgroup_exists",
    "pid_alive",
    "become_subreaper",
    "DEFAULT_FORWARDED_SIGNALS",
]

# 可转发的温和信号集合（SIGKILL/SIGSTOP 无法捕获，不能转发给处理器使用）
DEFAULT_FORWARDED_SIGNALS = (
    signal.SIGTERM,
    signal.SIGINT,
    signal.SIGHUP,
    signal.SIGQUIT,
    signal.SIGUSR1,
    signal.SIGUSR2,
)


# --------------------------------------------------------------------------- #
# 时钟：生产用单调实时钟；测试用 FakeClock 注入时间，宽限逻辑无需真实等待
# --------------------------------------------------------------------------- #
class RealClock:
    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class FakeClock:
    """测试时钟：sleep 只推进逻辑时间，立刻返回。"""

    def __init__(self) -> None:
        self._t = 0.0
        self.sleeps: List[float] = []

    def now(self) -> float:
        return self._t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._t += seconds


# -----------------------------------------------------------------------------
# /proc 扫描（Linux）：找出指定进程组内的全部存活/僵尸进程
# -----------------------------------------------------------------------------
@dataclass(frozen=True)
class ProcInfo:
    pid: int
    pgid: int
    ppid: int
    state: str  # R/S/D/Z/...
    comm: str

    @property
    def zombie(self) -> bool:
        return self.state == "Z"


def _parse_stat(path: str) -> Optional[ProcInfo]:
    try:
        with open(path, "r", encoding="ascii", errors="replace") as f:
            data = f.read()
    except (OSError, ValueError):
        return None
    # comm 可能包含空格/括号，最后一个 ')' 之后才是规范字段
    rparen = data.rfind(")")
    if rparen < 0:
        return None
    pid_part = data[: rparen].split(" ", 1)[0]
    fields = data[rparen + 2 :].split()
    if len(fields) < 4:
        return None
    try:
        pid = int(pid_part)
        state = fields[0]
        ppid = int(fields[1])
        pgrp = int(fields[2])
    except ValueError:
        return None
    comm = data[data.find("(") + 1 : rparen]
    return ProcInfo(pid=pid, pgid=pgrp, ppid=ppid, state=state, comm=comm)


def list_pgroup(pgid: int) -> List[ProcInfo]:
    """返回当前 pgid == *pgid* 的全部进程（含僵尸，便于校验零遗留）。"""
    out: List[ProcInfo] = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return out
    for name in entries:
        if not name.isdigit():
            continue
        info = _parse_stat("/proc/%s/stat" % name)
        if info is not None and info.pgid == pgid:
            out.append(info)
    out.sort(key=lambda p: p.pid)
    return out


def pgroup_exists(pgid: int) -> bool:
    """O(1) 组存活探针：killpg(pgid, 0) 不真正发信号，仅做存在性/权限检查。

    组内只要还有一个进程（含待回收僵尸）就返回 True；整组消失返回 False。
    """
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def pid_alive(pid: int, allow_zombie: bool = False) -> bool:
    info = _parse_stat("/proc/%d/stat" % pid)
    if info is None:
        return False
    return allow_zombie or not info.zombie


def become_subreaper() -> bool:
    """把当前进程设为 child subreaper（Linux >= 3.4）。

    成功后，会话内成为孤儿的子孙进程会被重parent到本进程而不是 PID 1，
    从而可以由本进程 waitpid 回收，清理校验时不留下等待 init 回收的窗口。
    失败（如非 Linux）时静默降级，init/systemd 仍会负责回收。
    """
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
        PR_SET_CHILD_SUBREAPER = 36
        rc = libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)
        return rc == 0
    except (OSError, AttributeError):
        return False


# -----------------------------------------------------------------------------
# 清理结果
# -----------------------------------------------------------------------------
@dataclass
class CleanupResult:
    pgid: int
    completed: bool = False                 # 整组是否已全部消失
    escalated: bool = False                 # 是否动用了 SIGKILL
    term_sent: List[int] = field(default_factory=list)   # SIGTERM 目标 pgid
    kill_sent: List[int] = field(default_factory=list)   # SIGKILL 目标 pgid
    term_phase_seconds: float = 0.0
    kill_phase_seconds: float = 0.0
    duration_seconds: float = 0.0
    survivors: List[ProcInfo] = field(default_factory=list)
    signals_during_cleanup: List[int] = field(default_factory=list)

    def summary(self) -> str:
        surv = ",".join(str(p.pid) for p in self.survivors) or "-"
        during = ",".join(str(s) for s in self.signals_during_cleanup) or "-"
        return (
            f"pgid={self.pgid} completed={self.completed} escalated={self.escalated} "
            f"term={self.term_phase_seconds:.3f}s kill={self.kill_phase_seconds:.3f}s "
            f"total={self.duration_seconds:.3f}s survivors=[{surv}] "
            f"signals_during_cleanup=[{during}]"
        )


# -----------------------------------------------------------------------------
# 被监管的进程树
# -----------------------------------------------------------------------------
class ProcessTree:
    """监管一个以独立会话/进程组启动的子进程及其全部后代。"""

    def __init__(
        self,
        argv: Sequence[str],
        *,
        clock: Optional[object] = None,
        term_timeout: float = 3.0,
        kill_timeout: float = 3.0,
        poll_interval: float = 0.02,
        forwarded_signals: Sequence[int] = DEFAULT_FORWARDED_SIGNALS,
        lister: Optional[Callable[[int], List[ProcInfo]]] = None,
        prober: Optional[Callable[[int], bool]] = None,
        group_killer: Optional[Callable[[int, int], None]] = None,
        reaper: Optional[Callable[[], None]] = None,
        popen_kwargs: Optional[dict] = None,
    ) -> None:
        self.argv = list(argv)
        self.clock = clock or RealClock()
        self.term_timeout = term_timeout
        self.kill_timeout = kill_timeout
        self.poll_interval = poll_interval
        self.forwarded_signals = tuple(forwarded_signals)
        self._lister = lister or list_pgroup
        self._prober = prober or pgroup_exists
        self._group_killer = group_killer or os.killpg
        self._reap_once = reaper or self._default_reap
        self._popen_kwargs = popen_kwargs or {}

        self.proc: Optional[subprocess.Popen] = None
        self.pgid: int = -1
        self._cleaning = False
        self.last_result: Optional[CleanupResult] = None
        self._signals_during_cleanup: List[int] = []
        self._installed: List[tuple] = []  # (signum, 旧handler)

    # ---------------- 生命周期 ---------------- #
    def start(self) -> int:
        kwargs = dict(
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # setsid: 新会话 + 新进程组，pgid=pid
        )
        kwargs.update(self._popen_kwargs)
        self.proc = subprocess.Popen(self.argv, **kwargs)
        self.pgid = self.proc.pid
        return self.pgid

    # ---------------- 信号转发 ---------------- #
    def install_signal_handlers(self) -> None:
        """在监管进程上安装转发处理器。

        收到转发范围内的信号：1) 若正在清理，只登记不打断；2) 否则转发给
        整个子进程组并立即启动清理。清理结束后恢复旧处理器，调用方可决定
        是否退出（本库不替调用方做决定）。
        """
        for signum in self.forwarded_signals:
            old = signal.getsignal(signum)
            self._installed.append((signum, old))
            signal.signal(signum, self._handle_signal)

    def restore_signal_handlers(self) -> None:
        for signum, old in self._installed:
            signal.signal(signum, old)
        self._installed.clear()

    def _handle_signal(self, signum: int, frame) -> None:  # noqa: ARG002
        # 清理进行中：登记后立即返回，绝不让并发信号中断清理流程
        if self._cleaning:
            self._signals_during_cleanup.append(int(signum))
            return
        self.forward(int(signum))
        self.last_result = self.cleanup(gentle_signal=int(signum),
                                        already_sent=True)

    def forward(self, signum: int) -> None:
        """把信号投递给整个子进程组（覆盖全部派生层）。"""
        if self.pgid < 0:
            return
        try:
            self._group_killer(self.pgid, signum)
        except ProcessLookupError:
            pass
        except OSError as exc:
            if exc.errno != errno.ESRCH:
                raise

    def survivors(self, include_zombies: bool = False) -> List[ProcInfo]:
        procs = self._lister(self.pgid) if self.pgid >= 0 else []
        if include_zombies:
            return procs
        return [p for p in procs if not p.zombie]

    # ---------------- 清理 ---------------- #
    def cleanup(
        self,
        gentle_signal: int = signal.SIGTERM,
        *,
        already_sent: bool = False,
    ) -> CleanupResult:
        """温和信号 -> 宽限 -> SIGKILL 兜底 -> 回收 -> 校验。可重入。"""
        if self._cleaning:
            # 并发触发（如清理途中又来信号）：不重启、不打断
            return CleanupResult(pgid=self.pgid, completed=False,
                                 signals_during_cleanup=list(
                                     self._signals_during_cleanup))
        self._cleaning = True
        started = self.clock.now()
        result = CleanupResult(pgid=self.pgid)
        try:
            if not already_sent:
                self._safe_killpg(gentle_signal)
                result.term_sent.append(self.pgid)

            phase_start = self.clock.now()
            gone = self._wait_group_gone(self.term_timeout, result)
            result.term_phase_seconds = self.clock.now() - phase_start

            if not gone:
                # 兜底：整组 SIGKILL，连忽略/屏蔽温和信号的进程也逃不掉
                result.escalated = True
                self._safe_killpg(signal.SIGKILL)
                result.kill_sent.append(self.pgid)
                phase_start = self.clock.now()
                self._wait_group_gone(self.kill_timeout, result)
                result.kill_phase_seconds = self.clock.now() - phase_start

            # 回收僵尸：直接子进程 + subreaper 身份下过继来的子孙
            self._reap_deadline()
            if self.proc is not None:
                try:
                    self.proc.wait(timeout=0)
                except Exception:
                    pass
            result.signals_during_cleanup = list(self._signals_during_cleanup)
            if self.pgid >= 0 and not self._group_gone():
                result.survivors = self._lister(self.pgid)
            else:
                result.survivors = []
            result.duration_seconds = self.clock.now() - started
            result.completed = not result.survivors
            return result
        finally:
            self._cleaning = False

    # ---------------- 内部 ---------------- #
    def _safe_killpg(self, signum: int) -> None:
        try:
            self._group_killer(self.pgid, signum)
        except ProcessLookupError:
            pass
        except OSError as exc:
            if exc.errno != errno.ESRCH:
                raise

    def _group_gone(self) -> bool:
        return not self._prober(self.pgid)

    def _wait_group_gone(self, timeout: float,
                         result: CleanupResult) -> bool:
        deadline = self.clock.now() + timeout
        while True:
            self._reap_once()
            if self._group_gone():
                return True
            remaining = deadline - self.clock.now()
            if remaining <= 0:
                return False
            self.clock.sleep(min(self.poll_interval, remaining))

    def _reap_deadline(self) -> None:
        deadline = self.clock.now() + self.kill_timeout
        while self.clock.now() < deadline:
            self._reap_once()
            if self._group_gone():
                return
            self.clock.sleep(min(self.poll_interval, deadline - self.clock.now()))

    @staticmethod
    def _default_reap() -> None:
        """回收任意已死子进程（WNOHANG，非阻塞）。"""
        while True:
            try:
                done_pid, _status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return
            except OSError as exc:
                if exc.errno == errno.ECHILD:
                    return
                raise
            if done_pid == 0:
                return
