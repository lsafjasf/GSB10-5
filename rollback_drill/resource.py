"""Resource metering (stdlib only).

Tracks wall time, bytes copied, file count and Python memory delta.  RSS is
read from ``/proc`` when available, otherwise ``resource.getrusage`` is used
on POSIX.  All meters are additive so 100-run benchmarks can aggregate them.
"""

import os
import resource
import time
from dataclasses import dataclass, field


def rss_bytes() -> int:
    try:
        with open(f"/proc/{os.getpid()}/statm") as fh:
            pages = int(fh.read().split()[1])
        return pages * resource.getpagesize()
    except OSError:
        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024)


@dataclass
class ResourceMeter:
    bytes_copied: int = 0
    files_touched: int = 0
    operations: int = 0
    rss_start: int = field(default_factory=rss_bytes)
    rss_peak: int = 0
    wall_start: float = field(default_factory=time.perf_counter)

    def add_bytes(self, n: int) -> None:
        self.bytes_copied += n
        self.operations += 1
        peak = rss_bytes()
        if peak > self.rss_peak:
            self.rss_peak = peak

    def add_files(self, n: int) -> None:
        self.files_touched += n

    def snapshot(self, label: str = "") -> dict[str, float]:
        rss = rss_bytes()
        if rss > self.rss_peak:
            self.rss_peak = rss
        return {
            "bytes_copied": float(self.bytes_copied),
            "files_touched": float(self.files_touched),
            "operations": float(self.operations),
            "rss_peak_bytes": float(max(self.rss_peak, self.rss_start)),
            "rss_delta_bytes": float(self.rss_peak - self.rss_start),
            "wall_elapsed_ms": (time.perf_counter() - self.wall_start) * 1000.0,
        }
