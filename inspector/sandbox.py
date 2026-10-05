"""单条巡检规则的沙箱执行。

隔离手段（仅标准库）：
- AST 静态校验：禁止 import、禁止任何以 '_' 开头的名字/属性（堵 dunder 逃逸）。
- 受限 builtins：只放行纯计算内建函数，无 open/eval/exec/compile/__import__。
- 受限模块：仅注入 re / math / json 三个纯计算模块。
- 资源上界：RLIMIT_AS（内存）、RLIMIT_CPU（CPU 秒，兜底）、父进程墙上时钟超时 SIGKILL。
- 输出上界：子进程 stdout/stderr 重定向到带容量上限的 writer，超限即中止规则。
"""
from __future__ import annotations

import ast
import io
import json
import math
import multiprocessing as mp
import re
import resource
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

STATUS_OK = "OK"                      # check() 返回真
STATUS_FAILED = "FAILED"              # check() 返回假（业务判定不通过）
STATUS_ERROR = "ERROR"                # 规则抛异常
STATUS_TIMEOUT = "TIMEOUT"            # 超墙上时钟 / CPU 秒被强杀
STATUS_MEMORY = "MEMORY_EXCEEDED"     # 触发 RLIMIT_AS
STATUS_OUTPUT = "OUTPUT_EXCEEDED"     # 打印输出超上限
STATUS_REJECTED = "REJECTED"          # 静态校验不通过（未执行）
STATUS_CRASHED = "CRASHED"            # 子进程异常死亡


@dataclass(frozen=True)
class Limits:
    wall_time_s: float = 2.0      # 墙上时钟上限（主判据，父进程强制执行）
    cpu_time_s: int = 2           # CPU 秒上限（兜底，防父进程失联）
    memory_mb: int = 128          # 地址空间上限 RLIMIT_AS
    output_bytes: int = 64 * 1024   # 规则 print 输出上限
    result_bytes: int = 16 * 1024   # check() 返回值序列化上限


DEFAULT_LIMITS = Limits()

# 能力清单（白名单）：纯计算内建函数，无任何 I/O、无动态执行、无内省逃逸口。
SAFE_BUILTINS: dict[str, Any] = {
    name: __builtins__[name] if isinstance(__builtins__, dict) else getattr(__builtins__, name)
    for name in (
        "abs", "all", "any", "bin", "bool", "chr", "dict", "divmod",
        "enumerate", "filter", "float", "format", "frozenset", "hex",
        "int", "isinstance", "len", "list", "map", "max", "min", "oct",
        "ord", "pow", "print", "range", "repr", "round", "set", "slice",
        "sorted", "str", "sum", "tuple", "zip",
        "Exception", "ValueError", "TypeError", "KeyError", "IndexError",
        "ZeroDivisionError", "StopIteration",
    )
}

# 能力清单（模块）：纯计算，无系统访问。
SAFE_MODULES: dict[str, Any] = {"re": re, "math": math, "json": json}


class RuleRejected(Exception):
    """规则未通过静态安全校验。"""


class OutputLimitExceeded(Exception):
    """规则输出超过上限。"""


def validate_source(source: str) -> ast.AST:
    """静态校验：拒绝 import 与一切下划线开头的名字/属性。"""
    try:
        tree = ast.parse(source, mode="exec")
    except SyntaxError as exc:
        raise RuleRejected(f"syntax error: {exc}") from exc
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise RuleRejected("import is not allowed")
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            raise RuleRejected("global/nonlocal is not allowed")
        if isinstance(node, ast.Name) and node.id.startswith("_"):
            raise RuleRejected(f"name {node.id!r} is not allowed")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise RuleRejected(f"attribute {node.attr!r} is not allowed")
    return tree


class _CappedWriter(io.TextIOBase):
    """带容量上限的 stdout/stderr 替代品，超限抛 OutputLimitExceeded 中止规则。"""

    def __init__(self, limit: int):
        self._limit = limit
        self._chunks: list[str] = []
        self._size = 0

    def writable(self) -> bool:
        return True

    def write(self, s: Any) -> int:
        s = str(s)
        remaining = self._limit - self._size
        if remaining <= 0:
            raise OutputLimitExceeded(f"output exceeds {self._limit} bytes")
        if len(s) > remaining:
            self._chunks.append(s[:remaining])
            self._size = self._limit
            raise OutputLimitExceeded(f"output exceeds {self._limit} bytes")
        self._chunks.append(s)
        self._size += len(s)
        return len(s)

    def getvalue(self) -> str:
        return "".join(self._chunks)


def _child_main(rule_id: str, source: str, facts: Any, limits: Limits, conn) -> None:
    """子进程入口：先设 rlimit，再在校验+受限全局下执行规则。"""
    writer: Optional[_CappedWriter] = None
    status, value, error = STATUS_ERROR, None, None
    try:
        mem = limits.memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_time_s, limits.cpu_time_s + 1))

        tree = validate_source(source)
        code = compile(tree, f"<rule {rule_id}>", "exec")

        writer = _CappedWriter(limits.output_bytes)
        sys.stdout = writer
        sys.stderr = writer

        globals_dict: dict[str, Any] = {"__builtins__": dict(SAFE_BUILTINS)}
        globals_dict.update(SAFE_MODULES)
        exec(code, globals_dict)  # noqa: S102 - 沙箱内有意为之

        check = globals_dict.get("check")
        if not callable(check):
            raise RuleRejected("rule must define a callable check(facts)")
        raw = check(facts)
        status = STATUS_OK if bool(raw) else STATUS_FAILED
        value = repr(raw)[: limits.result_bytes]
    except OutputLimitExceeded as exc:
        status, error = STATUS_OUTPUT, str(exc)
    except MemoryError:
        status, error = STATUS_MEMORY, f"address space limit {limits.memory_mb} MB exceeded"
    except RuleRejected as exc:
        status, error = STATUS_REJECTED, str(exc)
    except BaseException as exc:  # 规则自身的异常，隔离在本进程
        status, error = STATUS_ERROR, f"{type(exc).__name__}: {exc}"

    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    try:
        conn.send({
            "rule_id": rule_id, "status": status, "value": value,
            "error": error, "output": writer.getvalue() if writer else "",
            "max_rss_kb": rss_kb,
        })
    except Exception:
        pass
    finally:
        conn.close()


def run_rule(rule_id: str, source: str, facts: Any = None,
             limits: Limits = DEFAULT_LIMITS) -> dict:
    """在独立子进程中执行一条规则，永远返回结构化结果，绝不抛出。"""
    ctx = mp.get_context("fork")
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_child_main,
                       args=(rule_id, source, facts, limits, child_conn),
                       daemon=True)
    start = time.monotonic()
    proc.start()
    child_conn.close()

    # 独立线程读结果管道：消息可能大于管道缓冲，若等子进程退出再读会死锁。
    inbox: list[dict] = []

    def _reader() -> None:
        try:
            inbox.append(parent_conn.recv())
        except (EOFError, OSError):
            pass  # 子进程被强杀，管道已断

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()

    proc.join(limits.wall_time_s)
    duration_ms = (time.monotonic() - start) * 1000.0

    timed_out = proc.is_alive()
    if timed_out:
        proc.kill()  # SIGKILL，死循环到此为止
    proc.join()
    exitcode = proc.exitcode

    reader.join(timeout=1.0)  # 正常退出的子进程，消息已在管道中
    msg = inbox[0] if inbox else None
    parent_conn.close()

    if msg is None:
        if timed_out or exitcode == -9 or exitcode == -24:  # SIGKILL / SIGXCPU
            msg = {"rule_id": rule_id, "status": STATUS_TIMEOUT, "value": None,
                   "error": f"killed after {limits.wall_time_s}s wall-clock limit",
                   "output": "", "max_rss_kb": None}
        else:
            msg = {"rule_id": rule_id, "status": STATUS_CRASHED, "value": None,
                   "error": f"worker died, exitcode={exitcode}",
                   "output": "", "max_rss_kb": None}
    msg["duration_ms"] = round(duration_ms, 3)
    return msg
