"""内置自动步骤处理器（仅标准库），供手册编排复用。"""
from __future__ import annotations

import hashlib
import os
import time
from typing import Any, Dict

from .models import Context, Step


def log_message(step: Step, ctx: Context) -> Dict[str, Any]:
    msg = step.params.get("message", step.title)
    return {"logged": msg}


def check_service(step: Step, ctx: Context) -> Dict[str, Any]:
    """模拟健康检查：params.healthy=False 时判定失败。"""
    healthy = step.params.get("healthy", True)
    if not healthy:
        raise RuntimeError(f"服务 {step.params.get('service', '?')} 健康检查未通过")
    return {"service": step.params.get("service", "?"), "healthy": True}


def restart_service(step: Step, ctx: Context) -> Dict[str, Any]:
    svc = step.params.get("service", "?")
    return {"service": svc, "restarted": True}


def sleep_ms(step: Step, ctx: Context) -> Dict[str, Any]:
    ms = int(step.params.get("ms", 0))
    time.sleep(ms / 1000.0)
    return {"slept_ms": ms}


def write_file(step: Step, ctx: Context) -> Dict[str, Any]:
    path = step.params["path"]
    content = step.params.get("content", "")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return {"path": path, "bytes": len(content.encode("utf-8"))}


def verify_file(step: Step, ctx: Context) -> Dict[str, Any]:
    path = step.params["path"]
    if not os.path.exists(path):
        raise RuntimeError(f"文件不存在: {path}")
    with open(path, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    return {"path": path, "sha256": digest}


def noop(step: Step, ctx: Context) -> Dict[str, Any]:
    return {"ok": True}
