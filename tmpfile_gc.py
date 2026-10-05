"""tmpfile_gc - 临时文件命名与回收库（仅标准库）。

命名规则（目录 + 文件名双重定位，唯一定位到实例与用途）：

    <root>/<instance_id>/<purpose>--<instance_id>--<pid>--<seq:06d>--<token8hex>.tmp

    root         应用级临时目录，例如 /tmp/gsb1tmp
    instance_id  实例唯一标识：<hostname>-p<pid>-<启动时间UTC>-<4hex随机>
                 例如 web01-p4210-20261006T021530Z-a3f9
    purpose      用途标识，例如 upload / cache / export
    pid          创建者进程号
    seq          进程内单调递增序号（6 位，前导零）
    token        8 位十六进制随机串（防碰撞）

样例：
    /tmp/gsb1tmp/web01-p4210-20261006T021530Z-a3f9/
        heartbeat.json
        upload--web01-p4210-20261006T021530Z-a3f9--4210--000017--9c2bd4e1.tmp

回收规则（只动自己 + 只收死人残留 + 不动在用的）：
    1. sweep()       只清理本实例目录下、文件名实例字段等于本实例、
                     mtime 早于 min_age、且 flock 可获取的文件。
    2. reap_orphans() 扫描所有实例目录，仅当该实例“已死亡”才回收：
        死亡判定 = heartbeat.json 的 mtime 超过 heartbeat_ttl
                   且 (pid 不存在 或 /proc/<pid> 启动时间与记录不符)。
        回收前仍逐文件做 flock 占用检查与 min_age 宽限。
    3. 正在使用的判定依据（三重）：
        - 句柄/锁：创建者持有 flock(LOCK_EX)，回收方 LOCK_EX|LOCK_NB 抢不到即跳过；
        - 时间：  mtime 距今不足 min_age 的一律跳过；
        - 锁/租约：实例 heartbeat 未过期即视为存活，整目录不碰。
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import socket
import threading
import time
from pathlib import Path

_COMPONENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_NAME_RE = re.compile(
    r"^(?P<purpose>[A-Za-z0-9._-]+)--(?P<inst>[A-Za-z0-9._-]+)--(?P<pid>\d+)"
    r"--(?P<seq>\d{6})--(?P<token>[0-9a-f]{8})\.tmp$"
)
HEARTBEAT_NAME = "heartbeat.json"


def _check_component(value: str, what: str) -> str:
    if not value or not _COMPONENT_RE.match(value) or "--" in value:
        raise ValueError(f"非法{what}: {value!r}（只允许 [A-Za-z0-9._-] 且不得包含 '--'）")
    return value


def _pid_start_time(pid: int):
    """Linux: /proc/<pid>/stat 第 22 字段（启动时钟滴答）。非 Linux 返回 None。"""
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            data = fh.read()
        return int(data[data.rindex(b")") + 2:].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def parse_name(name: str):
    """解析临时文件名，不匹配返回 None，匹配返回 dict。"""
    match = _NAME_RE.match(name)
    if not match:
        return None
    return match.groupdict()


def _flock_free(path: Path) -> bool:
    """能以非阻塞方式抢到排他锁 => 无人占用。"""
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    finally:
        os.close(fd)


class TempFileManager:
    """单实例的临时文件管理器。每实例一个，进程内可线程共享。"""

    def __init__(self, root, instance_id=None, *, heartbeat_ttl=30.0,
                 heartbeat_interval=None, min_age=60.0, heartbeat=True):
        self.root = Path(root)
        if instance_id is None:
            instance_id = "%s-p%d-%s-%s" % (
                socket.gethostname(), os.getpid(),
                time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()),
                secrets.token_hex(2))
        self.instance_id = _check_component(instance_id, "实例ID")
        self.dir = self.root / self.instance_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.min_age = float(min_age)
        self.heartbeat_ttl = float(heartbeat_ttl)
        self.heartbeat_interval = float(
            heartbeat_interval if heartbeat_interval is not None
            else max(heartbeat_ttl / 3.0, 0.1))
        self._seq = 0
        self._seq_lock = threading.Lock()
        self._stop = threading.Event()
        self._hb_thread = None
        self._write_heartbeat()
        if heartbeat:
            self._hb_thread = threading.Thread(
                target=self._heartbeat_loop, name="tmpgc-heartbeat", daemon=True)
            self._hb_thread.start()

    # ---------- 命名与创建 ----------

    def new_path(self, purpose: str) -> Path:
        purpose = _check_component(purpose, "用途")
        with self._seq_lock:
            self._seq += 1
            seq = self._seq
        name = "%s--%s--%d--%06d--%s.tmp" % (
            purpose, self.instance_id, os.getpid(), seq, secrets.token_hex(4))
        return self.dir / name

    def new_file(self, purpose: str):
        """创建并独占锁定一个临时文件，返回二进制读写文件对象（关闭即释放锁）。"""
        path = self.new_path(purpose)
        fh = open(path, "w+b")  # O_EXCL 语义由随机 token + 序号保证
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fh

    # ---------- 心跳 / 存活判定 ----------

    def _heartbeat_payload(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "pid": os.getpid(),
            "pid_start": _pid_start_time(os.getpid()),
            "ts": time.time(),
        }

    def _write_heartbeat(self):
        tmp = self.dir / (HEARTBEAT_NAME + ".tmp")
        tmp.write_text(json.dumps(self._heartbeat_payload()))
        os.replace(tmp, self.dir / HEARTBEAT_NAME)

    def _heartbeat_loop(self):
        while not self._stop.wait(self.heartbeat_interval):
            try:
                self._write_heartbeat()
            except OSError:
                pass

    def close(self):
        self._stop.set()
        if self._hb_thread:
            self._hb_thread.join(timeout=2)

    @classmethod
    def instance_alive(cls, inst_dir: Path, heartbeat_ttl: float, now=None) -> bool:
        """心跳未过期 => 活；心跳过期但 pid 仍活且启动时间一致 => 保守视为活。"""
        now = time.time() if now is None else now
        hb = inst_dir / HEARTBEAT_NAME
        try:
            mtime = hb.stat().st_mtime
        except OSError:
            mtime = None
        if mtime is not None and now - mtime <= heartbeat_ttl:
            return True
        try:
            info = json.loads(hb.read_text())
        except (OSError, ValueError):
            return False
        pid = info.get("pid")
        if not isinstance(pid, int) or not _pid_alive(pid):
            return False
        recorded = info.get("pid_start")
        if recorded is None:
            return False  # 无法防伪 => 心跳已过期，按死亡处理
        current = _pid_start_time(pid)
        if current is None:
            return False
        return current == recorded  # pid 复用防护

    # ---------- 回收 ----------

    @staticmethod
    def _reclaim_file(path: Path, now, min_age, stats):
        try:
            st = path.stat()
        except OSError:
            return
        if now - st.st_mtime < min_age:
            stats["skipped_young"] += 1
            return
        if not _flock_free(path):
            stats["skipped_in_use"] += 1
            return
        try:
            path.unlink()
            stats["reclaimed"] += 1
        except FileNotFoundError:
            stats["raced"] += 1  # 被并发回收者抢先，正常

    def sweep(self, now=None) -> dict:
        """只清理本实例自己的过期文件。绝不触碰其他实例的文件。"""
        now = time.time() if now is None else now
        stats = {"reclaimed": 0, "skipped_young": 0, "skipped_in_use": 0,
                 "skipped_foreign": 0, "raced": 0}
        for entry in os.scandir(self.dir):
            if not entry.is_file(follow_symlinks=False):
                continue
            if entry.name == HEARTBEAT_NAME:
                continue
            info = parse_name(entry.name)
            if info is None or info["inst"] != self.instance_id:
                stats["skipped_foreign"] += 1
                continue
            self._reclaim_file(Path(entry.path), now, self.min_age, stats)
        return stats

    @classmethod
    def reap_orphans(cls, root, *, heartbeat_ttl=30.0, min_age=60.0,
                     exclude_instance=None, now=None) -> dict:
        """回收已死亡实例的残留。活实例目录一律不碰。"""
        now = time.time() if now is None else now
        root = Path(root)
        stats = {"reclaimed": 0, "skipped_young": 0, "skipped_in_use": 0,
                 "alive_dirs": 0, "dead_dirs": 0, "raced": 0}
        try:
            for entry in os.scandir(root):
                if not entry.is_dir(follow_symlinks=False):
                    continue
                if entry.name == exclude_instance:
                    continue
                inst_dir = Path(entry.path)
                if cls.instance_alive(inst_dir, heartbeat_ttl, now):
                    stats["alive_dirs"] += 1
                    continue
                stats["dead_dirs"] += 1
                for fentry in os.scandir(inst_dir):
                    if not fentry.is_file(follow_symlinks=False):
                        continue
                    if fentry.name == HEARTBEAT_NAME:
                        continue
                    if parse_name(fentry.name) is None:
                        continue
                    cls._reclaim_file(Path(fentry.path), now, min_age, stats)
                try:
                    (inst_dir / HEARTBEAT_NAME).unlink()
                    inst_dir.rmdir()  # 仅在空目录时成功
                except OSError:
                    pass
        finally:
            pass
        return stats
