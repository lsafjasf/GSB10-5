"""
tmpfile_lib.py -- 按实例隔离的临时文件命名与回收库（Python 3 标准库，仅 Linux/POSIX）

解决的问题
==========
同一台机器上多个实例共用临时目录时，按前缀删除会误删其他实例正在使用的文件。
本库用两级结构隔离：

    <tmp_root>/<app>/<instance_id>/             实例私有目录
        owner.lock                              实例生命周期锁（flock，内核随进程释放）
        owner.json                              实例元数据（pid / 主机 / 启动时刻 / 随机令牌）
        <purpose>-<seq:06d>-<rand8>.tmp         数据文件

回收三道校验（全部通过才允许删除单个文件）：
    1. 锁：owner.lock 必须能非阻塞拿到排他锁 —— 拿到说明持有进程已死
       （SIGKILL 时由内核自动释放 flock，天然识别强杀残留）。
    2. 句柄：(st_dev, st_ino) 不得出现在 /proc/*/fd 中 —— 任何进程还打开着就跳过。
    3. 时间：文件 mtime 必须早于宽限期（now - mtime >= grace_seconds）。

本实例自己的文件通过 TempInstance.cleanup() 回收；跨实例的 reclaim() 只能回收
"锁已释放" 的死实例目录，活实例（锁仍被持有）一律跳过，杜绝误删。
"""

from __future__ import annotations

import fcntl
import itertools
import json
import os
import platform
import re
import secrets
import socket
import time
from datetime import datetime, timezone

__all__ = ["TempInstance", "reclaim", "ReclaimReport", "TempFileError"]

_DEFAULT_ROOT = os.environ.get("TMPFILE_ROOT", "/tmp")
_INSTANCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_PURPOSE_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
META_NAME = "owner.json"
LOCK_NAME = "owner.lock"


class TempFileError(RuntimeError):
    pass


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _new_instance_id() -> str:
    host = socket.gethostname().split(".")[0][:16]
    return f"{host}-{os.getpid()}-{_utc_stamp()}-{secrets.token_hex(3)}"


def _proc_start_ticks(pid: int) -> int | None:
    """/proc/<pid>/stat 第 22 字段 starttime；用于 pid 复用的二次确认。"""
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            data = fh.read()
        comm_end = data.rfind(b")")
        fields = data[comm_end + 2:].split()
        return int(fields[19])
    except (OSError, IndexError, ValueError):
        return None


class TempInstance:
    """一个正在运行的实例对自己临时目录的句柄。"""

    def __init__(self, app: str, tmp_root: str | None = None,
                 instance_id: str | None = None):
        if not _INSTANCE_RE.match(app):
            raise ValueError(f"illegal app name: {app!r}")
        self.app = app
        self.tmp_root = os.path.abspath(tmp_root or _DEFAULT_ROOT)
        self.instance_id = instance_id or _new_instance_id()
        if not _INSTANCE_RE.match(self.instance_id):
            raise ValueError(f"illegal instance id: {self.instance_id!r}")
        self.dir = os.path.join(self.tmp_root, self.app, self.instance_id)
        os.makedirs(self.dir, exist_ok=True)

        self._lock_path = os.path.join(self.dir, LOCK_NAME)
        self._lock_fd = os.open(self._lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(self._lock_fd)
            raise TempFileError(f"instance dir already locked: {self.dir}") from exc

        self.meta = {
            "app": self.app,
            "instance_id": self.instance_id,
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
            "start_utc": datetime.now(timezone.utc).isoformat(),
            "start_ticks": _proc_start_ticks(os.getpid()),
            "token": secrets.token_hex(8),
        }
        meta_tmp = os.path.join(self.dir, META_NAME + ".new")
        with open(meta_tmp, "w", encoding="utf-8") as fh:
            json.dump(self.meta, fh, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(meta_tmp, os.path.join(self.dir, META_NAME))

        self._seq = itertools.count(1)
        self._files: list[str] = []
        self.closed = False

    def new_temp(self, purpose: str, suffix: str = ".tmp") -> tuple[str, int]:
        """新建数据文件，返回 (路径, 已打开的 fd，O_RDWR, mode 0600)。"""
        if self.closed:
            raise TempFileError("instance already closed")
        if not _PURPOSE_RE.match(purpose):
            raise ValueError(f"illegal purpose: {purpose!r}")
        if not re.fullmatch(r"\.[A-Za-z0-9]{1,8}", suffix):
            raise ValueError(f"illegal suffix: {suffix!r}")
        seq = next(self._seq)
        name = f"{purpose}-{seq:06d}-{secrets.token_hex(4)}{suffix}"
        path = os.path.join(self.dir, name)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        self._files.append(path)
        return path, fd

    def cleanup(self) -> list[str]:
        """回收本实例自己的全部文件并释放锁（正常退出路径）。"""
        if self.closed:
            return []
        removed = []
        for path in list(self._files):
            try:
                os.unlink(path)
                removed.append(path)
            except FileNotFoundError:
                pass
        self._files = []
        self.close()
        return removed

    def close(self):
        if self.closed:
            return
        fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
        os.close(self._lock_fd)
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# ------------------------------- 回收器 ------------------------------------

def _pid_alive(meta: dict) -> bool:
    """辅助判定：元数据中的进程是否仍是创建时的那个进程（防 pid 复用）。"""
    pid = meta.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    ticks = meta.get("start_ticks")
    if isinstance(ticks, int):
        return _proc_start_ticks(pid) == ticks
    return True


def _open_file_ids() -> set[tuple[int, int]]:
    """扫描 /proc/*/fd，返回当前所有进程正打开的普通文件 (dev, inode) 集合。"""
    ids: set[tuple[int, int]] = set()
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return ids
    for pid in pids:
        fd_dir = f"/proc/{pid}/fd"
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                st = os.stat(f"{fd_dir}/{fd}")
            except OSError:
                continue
            ids.add((st.st_dev, st.st_ino))
    return ids


class ReclaimReport:
    def __init__(self):
        self.scanned = 0
        self.active_skipped: list[dict] = []
        self.residue: list[dict] = []
        self.reclaimed_files: list[str] = []
        self.in_use_skipped: list[dict] = []
        self.recent_skipped: list[dict] = []
        self.removed_dirs: list[str] = []

    def to_dict(self) -> dict:
        return {
            "scanned": self.scanned,
            "active_skipped": self.active_skipped,
            "residue": self.residue,
            "reclaimed_files": self.reclaimed_files,
            "in_use_skipped": self.in_use_skipped,
            "recent_skipped": self.recent_skipped,
            "removed_dirs": self.removed_dirs,
        }


def _is_data_file(name: str) -> bool:
    return name not in (META_NAME, META_NAME + ".new", LOCK_NAME)


def reclaim(app: str, tmp_root: str | None = None,
            grace_seconds: float = 0.0,
            now: float | None = None,
            scan_open_handles: bool = True) -> ReclaimReport:
    """
    扫描 <tmp_root>/<app>/ 下的实例目录并回收死实例残留。

    判定顺序见模块文档：flock -> /proc 句柄 -> mtime 宽限期。
    活实例（锁拿不到）整个目录跳过，绝不触碰。
    """
    if not _INSTANCE_RE.match(app):
        raise ValueError(f"illegal app name: {app!r}")
    root = os.path.join(os.path.abspath(tmp_root or _DEFAULT_ROOT), app)
    report = ReclaimReport()
    if now is None:
        now = time.time()

    try:
        entries = sorted(os.listdir(root))
    except FileNotFoundError:
        return report

    open_ids = _open_file_ids() if scan_open_handles else set()

    for entry in entries:
        inst_dir = os.path.join(root, entry)
        if not os.path.isdir(inst_dir):
            continue
        report.scanned += 1
        lock_path = os.path.join(inst_dir, LOCK_NAME)
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                report.active_skipped.append({
                    "instance_id": entry,
                    "dir": inst_dir,
                    "reason": "lock-held",
                })
                continue

            meta = {}
            meta_path = os.path.join(inst_dir, META_NAME)
            try:
                with open(meta_path, encoding="utf-8") as fh:
                    meta = json.load(fh)
            except (OSError, json.JSONDecodeError):
                meta = {}

            if meta and _pid_alive(meta):
                report.active_skipped.append({
                    "instance_id": entry,
                    "dir": inst_dir,
                    "reason": "pid-alive",
                })
                continue

            report.residue.append({
                "instance_id": entry,
                "dir": inst_dir,
                "pid": meta.get("pid"),
                "hostname": meta.get("hostname"),
                "start_utc": meta.get("start_utc"),
                "reason": "lock-free" if meta else "lock-free/no-meta",
            })

            dir_files = []
            try:
                dir_files = [n for n in os.listdir(inst_dir)
                             if _is_data_file(n)]
            except OSError:
                pass

            for name in sorted(dir_files):
                path = os.path.join(inst_dir, name)
                try:
                    st = os.stat(path)
                except FileNotFoundError:
                    continue

                if open_ids and (st.st_dev, st.st_ino) in open_ids:
                    report.in_use_skipped.append({
                        "path": path,
                        "reason": "open-handle",
                        "inode": st.st_ino,
                    })
                    continue

                if now - st.st_mtime < grace_seconds:
                    report.recent_skipped.append({
                        "path": path,
                        "reason": "within-grace",
                        "age_seconds": round(now - st.st_mtime, 3),
                        "grace_seconds": grace_seconds,
                    })
                    continue

                os.unlink(path)
                report.reclaimed_files.append(path)

            for leftover in (META_NAME, META_NAME + ".new"):
                p = os.path.join(inst_dir, leftover)
                try:
                    os.unlink(p)
                except FileNotFoundError:
                    pass
            os.unlink(lock_path)
            try:
                os.rmdir(inst_dir)
                report.removed_dirs.append(inst_dir)
            except OSError:
                pass
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(lock_fd)

    return report
