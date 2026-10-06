"""并行测试环境隔离与回收库（仅标准库）。

隔离维度：
  1. 命名空间：每次运行(run)有唯一 run_id，每个用例(case)有唯一 case_id，
     所有资源名都挂在 run_id/case_id 命名空间下，互不冲突。
  2. 独立目录：BASE_DIR/run_<pid>_<ts>_<rand>/case_<case_id>/ 为用例私有目录。
  3. 独立端口：进程内端口注册表 + bind(0) 探测双重保证，用例独占一个 TCP 端口。
  4. 环境变量：每个用例持有独立的 env 字典（不污染进程级 os.environ）。

回收与降级：
  - cleanup() 按资源逐个回收，单个资源回收失败只记录 CleanupError 并写入
    残留日志(residue.log)，不抛异常、不影响后续用例。
  - 新一轮 TestRun.start() 时自动扫描并清理上一轮异常退出留下的残留
    （属主进程已死亡的 run 目录 + residue.log 中登记的路径）。
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

DEFAULT_BASE_DIR = Path(
    os.environ.get("GSB_TESTENV_ROOT", Path(tempfile.gettempdir()) / "gsb_testenv")
)
RESIDUE_LOG_NAME = "residue.log"

_port_lock = threading.Lock()
_ports_in_use: set = set()

_log_lock = threading.Lock()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def allocate_port() -> int:
    """分配一个空闲 TCP 端口：bind(0) 探测 + 进程内注册表去重。"""
    with _port_lock:
        for _ in range(200):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            finally:
                sock.close()
            if port not in _ports_in_use:
                _ports_in_use.add(port)
                return port
    raise RuntimeError("no free TCP port after 200 attempts")


def release_port(port: int) -> None:
    with _port_lock:
        _ports_in_use.discard(port)


@dataclass
class CleanupError:
    case_id: str
    resource: str
    error: str


class IsolatedEnv:
    """单个测试用例的隔离环境。"""

    def __init__(self, run: "TestRun", case_id: Optional[str] = None):
        self.run = run
        self.case_id = case_id or f"case_{uuid.uuid4().hex[:12]}"
        self.dir: Path = run.run_dir / self.case_id
        self.port: Optional[int] = None
        self.env: Dict[str, str] = {}
        self.prepared = False

    def prepare(self) -> "IsolatedEnv":
        self.dir.mkdir(parents=True, exist_ok=False)
        self.port = allocate_port()
        # 用例私有环境变量：复制进程环境 + 追加命名空间化变量，不写 os.environ
        self.env = dict(os.environ)
        self.env.update(
            {
                "GSB_CASE_ID": self.case_id,
                "GSB_CASE_DIR": str(self.dir),
                "GSB_CASE_PORT": str(self.port),
            }
        )
        self.prepared = True
        return self

    def cleanup(self) -> List[CleanupError]:
        """回收全部资源。单资源失败被降级为 CleanupError 记录，不中断其他资源回收。"""
        errors: List[CleanupError] = []
        if self.port is not None:
            try:
                release_port(self.port)
            except Exception as exc:  # noqa: BLE001 - 降级处理
                errors.append(CleanupError(self.case_id, "port", repr(exc)))
            finally:
                self.port = None
        if self.dir.exists():
            try:
                shutil.rmtree(self.dir)
            except Exception as exc:  # noqa: BLE001 - 降级处理
                errors.append(CleanupError(self.case_id, "dir", repr(exc)))
                self.run.record_residue(str(self.dir), repr(exc))
        self.prepared = False
        return errors

    def __enter__(self) -> "IsolatedEnv":
        return self.prepare()

    def __exit__(self, *exc) -> bool:
        self.cleanup()
        return False


class TestRun:
    """一轮测试运行：负责命名空间根目录、残留检测与清扫。"""

    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = Path(base_dir) if base_dir else DEFAULT_BASE_DIR
        self.run_id = f"run_{os.getpid()}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
        self.run_dir = self.base_dir / self.run_id
        self.residue_log = self.base_dir / RESIDUE_LOG_NAME
        self.sweep_report: Dict = {}

    # ---- 生命周期 ----

    def start(self) -> "TestRun":
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.sweep_report = self.sweep_residue()
        self.run_dir.mkdir(parents=True, exist_ok=False)
        (self.run_dir / "run.json").write_text(
            json.dumps({"run_id": self.run_id, "pid": os.getpid(), "ts": time.time()})
        )
        return self

    def new_env(self, case_id: Optional[str] = None) -> IsolatedEnv:
        return IsolatedEnv(self, case_id)

    # ---- 残留检测与清理 ----

    def record_residue(self, path: str, reason: str) -> None:
        entry = {"path": path, "reason": reason, "ts": time.time(), "pid": os.getpid()}
        with _log_lock:
            with open(self.residue_log, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")

    def sweep_residue(self) -> Dict:
        """检测并清理上一轮残留：属主进程已死亡的 run 目录 + 残留日志登记的路径。"""
        report = {
            "stale_runs_detected": [],
            "stale_runs_removed": [],
            "active_runs_kept": [],
            "logged_paths_detected": [],
            "logged_paths_removed": [],
            "errors": [],
        }
        # 1) 扫描历史 run 目录
        for child in sorted(self.base_dir.iterdir()):
            if not child.is_dir() or not child.name.startswith("run_"):
                continue
            meta_file = child / "run.json"
            pid = -1
            try:
                pid = json.loads(meta_file.read_text()).get("pid", -1)
            except Exception:
                pass
            if _pid_alive(pid):
                report["active_runs_kept"].append(str(child))
                continue
            report["stale_runs_detected"].append(str(child))
            try:
                shutil.rmtree(child)
                report["stale_runs_removed"].append(str(child))
            except Exception as exc:  # noqa: BLE001
                report["errors"].append(f"{child}: {exc!r}")
        # 2) 处理残留日志中登记的路径（上一轮回收失败的资源）
        remaining = []
        if self.residue_log.exists():
            with open(self.residue_log, encoding="utf-8") as fh:
                entries = [json.loads(line) for line in fh if line.strip()]
            for entry in entries:
                path = Path(entry["path"])
                report["logged_paths_detected"].append(str(path))
                try:
                    if path.is_dir():
                        shutil.rmtree(path)
                    elif path.exists():
                        path.unlink()
                    if not path.exists():
                        report["logged_paths_removed"].append(str(path))
                    else:
                        remaining.append(entry)
                except Exception as exc:  # noqa: BLE001
                    report["errors"].append(f"{path}: {exc!r}")
                    remaining.append(entry)
            with open(self.residue_log, "w", encoding="utf-8") as fh:
                for entry in remaining:
                    fh.write(json.dumps(entry) + "\n")
        return report
