"""自适应追踪采样率控制器（仅标准库）。

设计要点
========
1. 头部采样（head-based）+ 一致性哈希决策：
   采样决策只依赖 trace_id 的哈希值与当前采样率，
   同一条链路在任意节点、任意进程、任意时刻的决策都相同，
   因此一条链路要么整体保留、要么整体丢弃，绝不出现部分节点被采样。

2. 异常链路优先保留：
   链路上游节点发现错误后，通过链路上下文（如 W3C tracestate/baggage）
   向下游传播 error 标记（hint）。凡带 error 标记的链路 100% 保留，
   不占用哈希抽签，直接放行。

3. 自适应采样率（每个调整窗口重新计算，默认 1s）：
   输入：当前量 rps、上限 max_tps、错误率 error_rate
     error_rps   = rps * error_rate
     normal_rps  = rps - error_rps
     budget      = max(0, max_tps - error_rps)      # 错误链路先占预算
     rate        = min(1, budget / normal_rps)      # 正常链路分摊剩余预算
     if error_rate > error_threshold:               # 错误率骤升 -> 提升正常链路采样
         rate = min(1, rate * error_rate / error_threshold)
     rate = clamp(rate, min_rate, 1.0)
   因为速率每窗口由实时测量值直接重算（非缓慢爬升），
   流量突增/突降时最多 1 个窗口内跟上。
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import deque
from typing import Callable, Deque, Optional, Tuple

_HASH_SPACE = float(1 << 64)


def hash_unit_interval(trace_id: str) -> float:
    """把 trace_id 稳定映射到 [0, 1)。跨进程/跨节点确定，用于一致性决策。"""
    digest = hashlib.blake2b(trace_id.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") / _HASH_SPACE


class AdaptiveSampler:
    """自适应采样器。线程安全，决策为 O(1)，不保存任何单链路状态。"""

    def __init__(
        self,
        max_traces_per_sec: float,
        *,
        min_rate: float = 0.001,
        window_sec: float = 1.0,
        error_threshold: float = 0.05,
        history_size: int = 3600,
        time_source: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_traces_per_sec <= 0:
            raise ValueError("max_traces_per_sec must be > 0")
        if not 0 < min_rate <= 1:
            raise ValueError("min_rate must be in (0, 1]")
        self.max_tps = float(max_traces_per_sec)
        self.min_rate = float(min_rate)
        self.window_sec = float(window_sec)
        self.error_threshold = float(error_threshold)
        self._time = time_source

        self._lock = threading.Lock()
        self._window_start = self._time()
        self._trace_count = 0
        self._error_count = 0
        self._rate = 1.0
        # (window_end_time, rps, error_rate, rate)
        self.history: Deque[Tuple[float, float, float, float]] = deque(maxlen=history_size)

    # ---- 采样率计算（纯函数，便于测试与绘制调整曲线） ----

    def compute_rate(self, rps: float, error_rate: float) -> float:
        """根据当前量、上限、错误率计算目标采样率。"""
        if rps <= 0:
            return 1.0
        error_rps = rps * error_rate
        normal_rps = rps - error_rps
        budget = max(0.0, self.max_tps - error_rps)
        rate = 1.0 if normal_rps <= 0 else min(1.0, budget / normal_rps)
        if error_rate > self.error_threshold:
            rate = min(1.0, rate * (error_rate / self.error_threshold))
        return min(1.0, max(self.min_rate, rate))

    # ---- 运行时 ----

    @property
    def rate(self) -> float:
        with self._lock:
            return self._rate

    def _maybe_adjust(self, now: float) -> None:
        elapsed = now - self._window_start
        if elapsed < self.window_sec:
            return
        rps = self._trace_count / elapsed if elapsed > 0 else 0.0
        error_rate = self._error_count / self._trace_count if self._trace_count else 0.0
        self._rate = self.compute_rate(rps, error_rate)
        self.history.append((now, rps, error_rate, self._rate))
        self._window_start = now
        self._trace_count = 0
        self._error_count = 0

    def should_sample(self, trace_id: str, *, has_error: bool = False) -> bool:
        """对一条链路做采样决策。

        has_error 为随链路上下文传播的异常标记；异常链路直接保留。
        正常链路按 trace_id 哈希抽签，同 trace_id 决策恒定。
        """
        now = self._time()
        with self._lock:
            self._maybe_adjust(now)
            self._trace_count += 1
            if has_error:
                self._error_count += 1
            rate = self._rate
        if has_error:
            return True
        return hash_unit_interval(trace_id) < rate
