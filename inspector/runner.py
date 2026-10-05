"""父进程调度器：并发执行规则、强制超时、汇总结果。

每条规则一个独立子进程：
- 子进程内由 rlimit 提供 CPU/内存/fd/进程数硬上界；
- 父进程提供墙钟超时（SIGKILL，保证“一定停得下来”）；
- 子进程崩溃/被杀只会反映在它自己的结果里，绝不影响其它规则。
"""

import json
import os
import resource
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .limits import (
    MAX_OUTPUT_BYTES,
    RESULT_DELIMITER,
    RLIMIT_CPU_SEC,
    STATUS_BAD_RULE,
    STATUS_INTERNAL_ERROR,
    STATUS_OK,
    STATUS_RULE_ERROR,
    STATUS_TIMEOUT,
    WALL_TIMEOUT_SEC,
)
from .sandbox import RuleRejected, validate_source

_PKG_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CHILD_BOOT = (
    "import sys; sys.path.insert(0, {p!r}); "
    "from inspector.child import main; main()"
).format(p=_PKG_PARENT)

# 父进程从管道最多读这么多：输出上限 + 分隔符 + 结果 JSON 余量
_READ_CAP = MAX_OUTPUT_BYTES + 256 * 1024


@dataclass
class RuleResult:
    rule_id: str
    status: str
    error: str = None
    result: object = None
    output_bytes: int = 0        # 规则 print 的字节量（截断前）
    duration_ms: float = 0.0     # 墙钟耗时（含进程启动）

    @property
    def ok(self):
        return self.status == STATUS_OK


@dataclass
class Summary:
    total: int = 0
    counts: dict = field(default_factory=dict)
    results: list = field(default_factory=list)
    wall_ms: float = 0.0
    peak_rss_kb: int = 0

    def as_dict(self):
        return {
            "total": self.total,
            "counts": self.counts,
            "wall_ms": round(self.wall_ms, 1),
            "peak_rss_kb": self.peak_rss_kb,
        }


def _run_one(rule_id, source, data, timeout):
    """在独立子进程里跑一条规则，返回 RuleResult。绝不抛异常。"""
    start = time.monotonic()

    # 静态校验在父进程先做一遍：不合法的规则连子进程都不必起
    try:
        validate_source(source)
    except RuleRejected as exc:
        return RuleResult(rule_id=rule_id, status=STATUS_BAD_RULE,
                          error=str(exc),
                          duration_ms=(time.monotonic() - start) * 1000)

    request = json.dumps({"source": source, "data": data}).encode("utf-8")
    try:
        proc = subprocess.Popen(
            [sys.executable, "-I", "-S", "-c", _CHILD_BOOT],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={},  # 空环境：规则拿不到任何环境变量
        )
    except OSError as exc:
        return RuleResult(rule_id=rule_id, status=STATUS_INTERNAL_ERROR,
                          error=f"无法启动子进程: {exc}",
                          duration_ms=(time.monotonic() - start) * 1000)

    timed_out = False
    try:
        raw, _ = proc.communicate(input=request, timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()  # SIGKILL：保证停得下来
        raw, _ = proc.communicate()

    duration_ms = (time.monotonic() - start) * 1000

    if len(raw) > _READ_CAP:
        raw = raw[:_READ_CAP]

    if timed_out:
        return RuleResult(rule_id=rule_id, status=STATUS_TIMEOUT,
                          error=f"超过墙钟上界 {timeout}s，已 SIGKILL",
                          output_bytes=len(raw), duration_ms=duration_ms)

    # 拆出结果 JSON（分隔符之后）与规则 print 输出（分隔符之前）
    idx = raw.rfind(RESULT_DELIMITER)
    if idx < 0:
        # 没有结果：子进程被 rlimit 杀掉或崩溃。
        # returncode == -SIGKILL 且非墙钟超时 => RLIMIT_CPU 到点被内核杀掉，
        # 归类为 timeout（CPU 上界生效），语义比“异常退出”更准确。
        if proc.returncode == -9:
            return RuleResult(rule_id=rule_id, status=STATUS_TIMEOUT,
                              error=f"CPU 超过 {RLIMIT_CPU_SEC}s 硬上界，被 SIGKILL (RLIMIT_CPU)",
                              output_bytes=len(raw), duration_ms=duration_ms)
        err = f"子进程异常退出 (returncode={proc.returncode})"
        return RuleResult(rule_id=rule_id, status=STATUS_RULE_ERROR,
                          error=err, output_bytes=len(raw),
                          duration_ms=duration_ms)

    user_output = raw[:idx]
    try:
        payload = json.loads(raw[idx + len(RESULT_DELIMITER):].decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return RuleResult(rule_id=rule_id, status=STATUS_INTERNAL_ERROR,
                          error=f"结果解析失败: {exc}",
                          output_bytes=len(user_output),
                          duration_ms=duration_ms)

    return RuleResult(
        rule_id=rule_id,
        status=payload.get("status", STATUS_INTERNAL_ERROR),
        error=payload.get("error"),
        result=payload.get("result"),
        output_bytes=len(user_output),
        duration_ms=duration_ms,
    )


def run_rules(rules, data=None, workers=None, timeout=WALL_TIMEOUT_SEC):
    """并发执行规则列表。

    rules: [(rule_id, source), ...]
    返回 Summary。任何一条规则的任何失败都不会影响其它规则。
    """
    if workers is None:
        workers = min(8, (os.cpu_count() or 4))

    start = time.monotonic()
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(_run_one, rid, src, data, timeout)
            for rid, src in rules
        ]
        for fut in futures:
            results.append(fut.result())
    wall_ms = (time.monotonic() - start) * 1000

    counts = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1

    # ru_maxrss(RUSAGE_CHILDREN) = 所有已回收子进程中的最大常驻内存峰值
    # （Linux 语义），即“这批规则里单条规则吃过的最大内存”。
    peak_rss_kb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss

    return Summary(total=len(results), counts=counts, results=results,
                   wall_ms=wall_ms, peak_rss_kb=peak_rss_kb)


def percentile(sorted_vals, pct):
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * pct / 100
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def duration_distribution(results):
    """耗时分布：min / p50 / p90 / p99 / max（毫秒）。"""
    ds = sorted(r.duration_ms for r in results)
    return {
        "min": round(ds[0], 1) if ds else 0,
        "p50": round(percentile(ds, 50), 1),
        "p90": round(percentile(ds, 90), 1),
        "p99": round(percentile(ds, 99), 1),
        "max": round(ds[-1], 1) if ds else 0,
    }
