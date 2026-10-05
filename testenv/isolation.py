"""测试环境隔离与回收。

隔离维度
--------
1. 命名空间: 每个用例拥有全局唯一 namespace（run_id + 进程内自增序号 +
   uuid 后缀），所有目录、文件名、环境变量键、标记文件都带该 namespace，
   用例之间不会发生命名冲突。
2. 独立目录: 每个用例独占 ``<base>/<run_id>/<namespace>`` 目录，工作目录、
   数据文件、状态文件全部限制在该目录树下。
3. 独立端口: ``allocate_port()`` 通过绑定 ``127.0.0.1:0`` 让内核分配未占用
   端口，并在进程内注册表去重；可选 ``hold=True`` 保持 socket 占用直到回收，
   消除"分配后-绑定前"的竞态。
4. 环境变量: ``env_vars`` 参数生成带 namespace 前缀的键，回收时自动删除；
   ``isolated_env`` 上下文管理器还会临时切换 CWD 并快照/恢复 os.environ。

回收保证
--------
- ``destroy()`` 永不抛出异常：单资源回收失败被捕获并记入 ``reclaim_errors``，
  不影响其它资源和后续用例；失败的环境登记到待重试清单，由
  ``sweep_residue()`` 在下一轮兜底清理（降级处理）。
- ``sweep_residue()`` 通过元数据中的 pid 存活检测识别异常退出残留，
  pid 已死或元数据损坏的目录均判定为残留并清理。
"""

from __future__ import annotations

import atexit
import contextlib
import errno
import itertools
import json
import os
import shutil
import socket
import stat
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

META_FILENAME = ".testenv.json"
_ENV_PREFIX = "TESTENV_"
_DEFAULT_BASE = os.path.join(tempfile.gettempdir(), "testenv_isolation")

_seq = itertools.count(1)
_seq_lock = threading.Lock()

# 进程内全局注册表：namespace -> IsolatedEnv。线程安全。
_registry_lock = threading.Lock()
_registry: Dict[str, "IsolatedEnv"] = {}

# 回收失败、等待下一轮 sweep 兜底的环境路径。
_pending_reclaim_lock = threading.Lock()
_pending_reclaim: Set[str] = set()


class ReclaimError(Exception):
    """回收单个资源时记录的错误（不会向调用方抛出，仅存于 reclaim_errors）。"""


@dataclass
class SweepReport:
    """残留扫描结果。"""

    scanned: int = 0
    removed: List[str] = field(default_factory=list)
    live_skipped: List[str] = field(default_factory=list)
    failed: List[Dict[str, str]] = field(default_factory=list)
    dangling_removed: List[str] = field(default_factory=list)
    elapsed_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scanned": self.scanned,
            "removed": self.removed,
            "live_skipped": self.live_skipped,
            "failed": self.failed,
            "dangling_removed": self.dangling_removed,
            "elapsed_ms": round(self.elapsed_ms, 3),
        }


def _new_namespace() -> str:
    with _seq_lock:
        seq = next(_seq)
    return f"env-{os.getpid()}-{seq}-{uuid.uuid4().hex[:8]}"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _on_demoting_error(func, path, exc_info):
    """shutil.rmtree 的 onerror：处理只读目录导致的删除失败（chmod 后重试）。"""
    try:
        os.chmod(path, stat.S_IRWXU)
        func(path)
    except Exception:
        # 交给上层 destroy/sweep 捕获并降级，不向上抛。
        pass


def _force_remove(path: str) -> None:
    shutil.rmtree(path, onerror=_on_demoting_error)
    if os.path.exists(path):
        raise OSError(errno.EIO, f"目录仍存在，删除未完成: {path}")


def live_namespaces() -> Set[str]:
    """返回当前进程仍存活（未回收）的环境命名空间集合。"""
    with _registry_lock:
        return set(_registry)


class IsolatedEnv:
    """一个用例独占的隔离环境。"""

    def __init__(
        self,
        base_dir: Optional[str] = None,
        *,
        env_vars: Optional[Dict[str, str]] = None,
        register_atexit: bool = True,
    ) -> None:
        self.namespace = _new_namespace()
        self.base_dir = base_dir or _DEFAULT_BASE
        self.run_id = f"run-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.path = os.path.join(self.base_dir, self.run_id, self.namespace)
        self.workdir = os.path.join(self.path, "work")
        self.state_dir = os.path.join(self.path, "state")
        self.meta_path = os.path.join(self.path, META_FILENAME)
        self.marker = os.path.join(self.state_dir, "marker")

        os.makedirs(self.workdir, exist_ok=False)
        os.makedirs(self.state_dir, exist_ok=False)

        self._ports: List[int] = []
        self._held_sockets: List[socket.socket] = []
        self._env_keys: List[str] = []
        self._destroyed = False
        self.degraded = False
        self.reclaim_errors: List[Dict[str, str]] = []

        self._write_meta()
        with open(self.marker, "w", encoding="utf-8") as fh:
            fh.write(self.namespace)

        for key, value in (env_vars or {}).items():
            env_key = f"{_ENV_PREFIX}{self.namespace}_{key}"
            os.environ[env_key] = value
            self._env_keys.append(env_key)

        with _registry_lock:
            _registry[self.namespace] = self
        if register_atexit:
            atexit.register(self.destroy)

    # ------------------------------------------------------------------ meta
    def _write_meta(self) -> None:
        meta = {
            "namespace": self.namespace,
            "pid": os.getpid(),
            "run_id": self.run_id,
            "created_ts": time.time(),
            "python": os.sys.version.split()[0],
        }
        with open(self.meta_path, "w", encoding="utf-8") as fh:
            json.dump(meta, fh)

    # ----------------------------------------------------------------- ports
    def allocate_port(self, *, hold: bool = False) -> int:
        """从内核申请一个 127.0.0.1 上的空闲端口。

        hold=True 时保持 socket 打开（回收时统一关闭），杜绝释放后被他人
        抢占的竞态；否则立即关闭，由用例自己尽快 bind。
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            with _registry_lock:
                claimed = port in {p for e in _registry.values() for p in e._ports}
            if claimed:
                sock.close()
                raise OSError(errno.EADDRINUSE, f"端口 {port} 已被本进程其它环境占用")
            self._ports.append(port)
            if hold:
                self._held_sockets.append(sock)
            else:
                sock.close()
        except Exception:
            with contextlib.suppress(Exception):
                sock.close()
            raise
        return port

    # --------------------------------------------------------------- destroy
    def destroy(self) -> None:
        """回收全部资源。任何单项失败都被吞掉并记录，绝不抛出。"""
        if self._destroyed:
            return
        self._destroyed = True

        # 1) 释放持有的端口 socket
        for sock in self._held_sockets:
            try:
                sock.close()
            except Exception as exc:  # noqa: BLE001 - 降级：记录后继续
                self._record_error("port_socket", str(exc))

        # 2) 删除注入的环境变量
        for env_key in self._env_keys:
            try:
                os.environ.pop(env_key, None)
            except Exception as exc:  # noqa: BLE001
                self._record_error(f"env:{env_key}", str(exc))

        # 3) 删除目录树
        dir_left = False
        try:
            _force_remove(self.path)
        except Exception as exc:  # noqa: BLE001
            dir_left = True
            self._record_error("dir", str(exc))

        with _registry_lock:
            _registry.pop(self.namespace, None)

        if self.reclaim_errors:
            # 降级：标记为 degraded，登记到待重试清单，后续 sweep 兜底。
            self.degraded = True
            with _pending_reclaim_lock:
                _pending_reclaim.add(self.path)
        if not dir_left:
            with _pending_reclaim_lock:
                _pending_reclaim.discard(self.path)

    def _record_error(self, resource: str, message: str) -> None:
        self.reclaim_errors.append({"resource": resource, "error": message})

    # -------------------------------------------------------------- context
    def __enter__(self) -> "IsolatedEnv":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.destroy()


@contextlib.contextmanager
def isolated_env(env_vars: Optional[Dict[str, str]] = None, **kwargs: Any):
    """上下文：进入时创建环境并 chdir，退出时恢复 cwd/env 并回收。"""
    env = IsolatedEnv(env_vars=env_vars, **kwargs)
    old_cwd = os.getcwd()
    old_environ = dict(os.environ)
    try:
        os.chdir(env.workdir)
        yield env
    finally:
        with contextlib.suppress(Exception):
            os.chdir(old_cwd)
        os.environ.clear()
        os.environ.update(old_environ)
        env.destroy()


def destroy_all() -> Dict[str, Any]:
    """回收当前进程登记的全部环境，返回降级统计。"""
    with _registry_lock:
        envs = list(_registry.values())
    degraded = 0
    errors: List[Dict[str, str]] = []
    for env in envs:
        env.destroy()
        if env.degraded:
            degraded += 1
            for item in env.reclaim_errors:
                errors.append({"namespace": env.namespace, **item})
    return {
        "destroyed": len(envs),
        "degraded": degraded,
        "errors": errors,
    }


# --------------------------------------------------------------------- sweep
def _is_testenv_root_candidate(entries: List[str]) -> bool:
    return any(e.startswith("env-") or e.startswith("run-") for e in entries)


def sweep_residue(base_dir: Optional[str] = None, *, min_age_s: float = 1.0) -> SweepReport:
    """扫描并清理上一轮（异常退出/回收失败）残留。

    判定规则:
      - 目录形如 env-* 且元数据 pid 已死 -> 残留，删除；
      - 元数据损坏/缺失 -> dangling 残留，删除；
      - pid 仍存活 -> 跳过（其它进程正在使用）；
      - 刚创建（< min_age_s）的跳过，避免和正在初始化的环境抢目录。
    同时清理本进程 destroy 降级后登记的待重试路径。
    """
    report = SweepReport()
    start = time.perf_counter()
    base = os.path.abspath(base_dir or _DEFAULT_BASE)

    # 先重试本进程回收失败的路径
    with _pending_reclaim_lock:
        retry_paths = sorted(_pending_reclaim)
    for path in retry_paths:
        try:
            if os.path.isdir(path):
                _force_remove(path)
            if not os.path.exists(path):
                _pending_reclaim.discard(path)
                report.removed.append(path)
        except Exception as exc:  # noqa: BLE001
            report.failed.append({"path": path, "error": str(exc)})

    if not os.path.isdir(base):
        report.elapsed_ms = (time.perf_counter() - start) * 1000
        return report

    now = time.time()
    for run_dir in sorted(os.listdir(base)):
        run_path = os.path.join(base, run_dir)
        if not os.path.isdir(run_path):
            continue
        try:
            namespaces = sorted(os.listdir(run_path))
        except OSError:
            continue
        for name in namespaces:
            env_path = os.path.join(run_path, name)
            if not os.path.isdir(env_path) or not name.startswith("env-"):
                continue
            report.scanned += 1
            meta_file = os.path.join(env_path, META_FILENAME)
            residue = False
            reason_pid: Optional[int] = None
            try:
                with open(meta_file, encoding="utf-8") as fh:
                    meta = json.load(fh)
                pid = int(meta["pid"])
                reason_pid = pid
                created_ts = float(meta.get("created_ts", 0))
                if now - created_ts < min_age_s:
                    report.live_skipped.append(env_path)
                    continue
                if not _pid_alive(pid):
                    residue = True
                else:
                    report.live_skipped.append(env_path)
            except Exception:
                # 元数据缺失/损坏：视为残留
                residue = True

            if not residue:
                continue
            try:
                _force_remove(env_path)
                if reason_pid is None:
                    report.dangling_removed.append(env_path)
                else:
                    report.removed.append(env_path)
                with _pending_reclaim_lock:
                    _pending_reclaim.discard(env_path)
            except Exception as exc:  # noqa: BLE001
                report.failed.append({"path": env_path, "error": str(exc)})

        # 清空的 run 目录顺手删除（失败可忽略）
        with contextlib.suppress(OSError):
            if not os.listdir(run_path):
                os.rmdir(run_path)

    with contextlib.suppress(OSError):
        if os.path.isdir(base) and not os.listdir(base):
            os.rmdir(base)

    report.elapsed_ms = (time.perf_counter() - start) * 1000
    return report
