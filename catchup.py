"""停机后定时任务补偿库。

仅依赖 Python 3 标准库；所有“当前时间”均由调用方注入，便于确定性测试。

核心概念
--------
- Task: 固定间隔周期任务，interval_seconds 对齐到 epoch 的整数倍时刻触发。
- 漏跑窗口: (last_run_at, now] 区间内应触发但未触发的全部计划时刻。
- 补偿压缩: 单任务最多保留 max_catchup_per_task 个“最近的”漏跑时刻，
  其余视为已过期不再补偿（避免停机一天就补一整天、把系统压垮）。
- 幂等: 每次补偿以 (task_id, scheduled_at) 为去重键写入追加日志，
  重复执行不会重复产生副作用。
- 续跑: 补偿过程中再次停机后，重启时重新生成计划并用日志中已完成的键过滤，
  只执行剩余部分。
- 上界: 全局速率上界 rate_per_sec 与并发上界 max_concurrency，
  simulate_load() 给出补偿期间每秒执行数的负载曲线。
"""

from __future__ import annotations

import heapq
import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Task:
    """固定间隔周期任务。

    last_run_at: 最近一次“已实际执行”的计划触发时刻（含正常调度与补偿）。
    """

    task_id: str
    interval_seconds: int
    last_run_at: float

    def __post_init__(self) -> None:
        if self.interval_seconds <= 0:
            raise ValueError("interval_seconds 必须为正整数")


@dataclass(frozen=True)
class CatchupItem:
    """一条补偿项：某任务在某计划时刻的一次漏跑。"""

    task_id: str
    scheduled_at: float

    @property
    def key(self) -> str:
        return f"{self.task_id}@{int(self.scheduled_at)}"


@dataclass
class TaskCatchupStat:
    missed: int  # 漏跑窗口总数（压缩前）
    planned: int  # 实际计划补偿次数（压缩后）
    dropped: int  # 因压缩策略放弃的次数
    times: list[float] = field(default_factory=list)  # 补偿计划时刻


@dataclass
class CatchupPlan:
    generated_at: float
    items: list[CatchupItem]  # 按 (scheduled_at, task_id) 排序
    per_task: dict[str, TaskCatchupStat]

    @property
    def total_items(self) -> int:
        return len(self.items)

    @property
    def total_dropped(self) -> int:
        return sum(s.dropped for s in self.per_task.values())


def missed_slots(
    interval_seconds: int,
    last_run_at: float,
    now: float,
) -> list[float]:
    """识别 (last_run_at, now] 内的全部计划触发时刻。"""
    return missed_window(interval_seconds, last_run_at, now)[1]


def missed_window(
    interval_seconds: int,
    last_run_at: float,
    now: float,
    max_keep: int | None = None,
) -> tuple[int, list[float]]:
    """识别漏跑窗口，返回 (漏跑总数, 最近的若干计划时刻)。

    漏跑总数用整除计算，不物化中间时刻；max_keep 给定时只物化最近的
    max_keep 个时刻，避免长时间停机（如每分钟任务停一天 = 1440 个）带来
    的无谓内存与耗时。
    """
    if now <= last_run_at:
        return 0, []
    first = (int(last_run_at // interval_seconds) + 1) * interval_seconds
    if first > now:
        return 0, []
    count = int((int(now) - first) // interval_seconds) + 1
    keep = count if max_keep is None else min(count, max_keep)
    start_index = count - keep
    times = [
        float(first + (start_index + i) * interval_seconds) for i in range(keep)
    ]
    return count, times


def build_plan(tasks: list[Task], now: float, max_catchup_per_task: int = 3) -> CatchupPlan:
    """为全部任务生成补偿计划。

    每个任务最多补偿 max_catchup_per_task 次，只保留漏跑窗口中最近的若干时刻；
    若漏跑数恰好不超过上界则全部保留。
    """
    if max_catchup_per_task < 1:
        raise ValueError("max_catchup_per_task 至少为 1")

    items: list[CatchupItem] = []
    per_task: dict[str, TaskCatchupStat] = {}

    for task in tasks:
        missed, planned_slots = missed_window(
            task.interval_seconds, task.last_run_at, now, max_catchup_per_task
        )
        dropped = missed - len(planned_slots)
        per_task[task.task_id] = TaskCatchupStat(
            missed=missed,
            planned=len(planned_slots),
            dropped=dropped,
            times=planned_slots,
        )
        items.extend(CatchupItem(task.task_id, t) for t in planned_slots)

    items.sort(key=lambda item: (item.scheduled_at, item.task_id))
    return CatchupPlan(generated_at=now, items=items, per_task=per_task)


def simulate_load(
    n_items: int,
    rate_per_sec: float,
    max_concurrency: int,
    duration_seconds: float,
    start_at: float,
) -> tuple[list[int], float]:
    """按速率/并发上界模拟补偿执行，返回 (每秒执行数曲线, 结束时刻)。

    - 速率上界: 相邻两个任务的开始时间至少间隔 1/rate_per_sec；
    - 并发上界: max_concurrency 个 worker，单任务执行 duration_seconds；
    任务开始时间取“速率允许时刻”与“最早空闲 worker 时刻”的较大值。
    """
    if rate_per_sec <= 0 or max_concurrency < 1 or duration_seconds < 0:
        raise ValueError("非法的速率/并发/耗时参数")
    if n_items == 0:
        return [], start_at

    workers = [start_at] * min(max_concurrency, n_items)
    heapq.heapify(workers)
    rate_slot = start_at
    counts: dict[int, int] = {}
    finish = start_at

    for _ in range(n_items):
        earliest_free = heapq.heappop(workers)
        begin = max(rate_slot, earliest_free)
        rate_slot = begin + 1.0 / rate_per_sec
        end = begin + duration_seconds
        heapq.heappush(workers, end)
        finish = max(finish, end)
        # 1e-9 抵消 1/rate 累加的浮点误差，避免边界时刻落入上一秒桶
        second = int(begin + 1e-9)
        counts[second] = counts.get(second, 0) + 1

    curve = [counts.get(sec, 0) for sec in range(int(start_at), int(finish) + 1)]
    return curve, finish


class IdempotencyStore:
    """补偿去重键的持久化日志（每行一个 key，append-only，崩溃安全）。"""

    def __init__(self, path: str | None = None) -> None:
        self.path = path
        self._keys: set[str] = set()
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    key = line.strip()
                    if key:
                        self._keys.add(key)

    def __contains__(self, key: str) -> bool:
        return key in self._keys

    def __len__(self) -> int:
        return len(self._keys)

    def mark(self, key: str) -> bool:
        """记录键。返回 False 表示键已存在（重复补偿，应跳过副作用）。"""
        if key in self._keys:
            return False
        self._keys.add(key)
        if self.path:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(key + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        return True


class SimulatedCrash(Exception):
    """模拟补偿期间再次停机（进程被杀）。"""


@dataclass
class RunStats:
    executed: int = 0  # 本次实际执行（产生副作用）的次数
    skipped_duplicate: int = 0  # 命中幂等日志而跳过的次数
    side_effects: int = 0  # handler 声明产生的副作用总数

    def as_dict(self) -> dict[str, int]:
        return {
            "executed": self.executed,
            "skipped_duplicate": self.skipped_duplicate,
            "side_effects": self.side_effects,
        }


class CatchupExecutor:
    """按补偿计划顺序执行补偿，带幂等保护。

    handler: callable(CatchupItem) -> int，执行副作用并返回副作用条数；
             抛 SimulatedCrash 可模拟补偿中再次停机（已 mark 的键保留在日志中）。
    crash_after: 执行满该数量后主动抛出 SimulatedCrash。
    """

    def __init__(self, store: IdempotencyStore) -> None:
        self.store = store

    def run(
        self,
        plan: CatchupPlan | list[CatchupItem],
        handler,
        crash_after: int | None = None,
    ) -> RunStats:
        items = plan.items if isinstance(plan, CatchupPlan) else plan
        stats = RunStats()
        for item in items:
            if not self.store.mark(item.key):
                stats.skipped_duplicate += 1
                continue
            produced = handler(item)
            stats.executed += 1
            stats.side_effects += int(produced or 0)
            if crash_after is not None and stats.executed >= crash_after:
                raise SimulatedCrash(
                    f"补偿中再次停机：已完成 {stats.executed} 项，键已持久化"
                )
        return stats


def remaining_plan(plan: CatchupPlan, store: IdempotencyStore) -> list[CatchupItem]:
    """从计划中剔除已在幂等日志里的项，得到二次停机重启后的剩余补偿计划。"""
    return [item for item in plan.items if item.key not in store]
