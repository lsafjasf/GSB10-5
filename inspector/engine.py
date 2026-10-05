"""巡检引擎：并发执行多条规则，单条失败不影响整体，产出汇总报告。"""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Optional

from .sandbox import DEFAULT_LIMITS, Limits, STATUS_OK, run_rule


@dataclass(frozen=True)
class Rule:
    rule_id: str
    source: str                 # 必须定义 check(facts)
    facts: Any = None           # 巡检采集到的事实数据
    limits: Optional[Limits] = None   # 可覆盖默认上界


@dataclass
class RuleResult:
    rule_id: str
    status: str
    value: Optional[str]
    error: Optional[str]
    output: str
    duration_ms: float
    max_rss_kb: Optional[int]


@dataclass
class Report:
    results: list[RuleResult] = field(default_factory=list)
    wall_time_s: float = 0.0

    @property
    def summary(self) -> dict:
        counts: dict[str, int] = {}
        for r in self.results:
            counts[r.status] = counts.get(r.status, 0) + 1
        return {
            "total": len(self.results),
            "by_status": counts,
            "all_passed": bool(self.results) and
                          all(r.status == STATUS_OK for r in self.results),
            "wall_time_s": round(self.wall_time_s, 3),
        }


class Engine:
    """每条规则一个独立子进程；线程池只负责并发地等子进程，不执行规则代码。"""

    def __init__(self, workers: Optional[int] = None, limits: Limits = DEFAULT_LIMITS):
        self.workers = workers or min(8, (os.cpu_count() or 1))
        self.limits = limits

    def run(self, rules: list[Rule]) -> Report:
        start = time.monotonic()
        report = Report()
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = [
                pool.submit(run_rule, r.rule_id, r.source, r.facts,
                            r.limits or self.limits)
                for r in rules
            ]
            for f in futures:
                msg = f.result()
                report.results.append(RuleResult(
                    rule_id=msg["rule_id"], status=msg["status"],
                    value=msg["value"], error=msg["error"],
                    output=msg["output"], duration_ms=msg["duration_ms"],
                    max_rss_kb=msg["max_rss_kb"],
                ))
        report.wall_time_s = time.monotonic() - start
        return report
