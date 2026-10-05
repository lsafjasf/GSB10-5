"""性能测量：流式验证大数据量，给出耗时与内存峰值。

内存指标：
- 进程级峰值 ru_maxrss（Linux 下单位 KiB）；
- 验证阶段当前 RSS 增量：读 /proc/self/status 的 VmRSS（当前值，
  区别于 ru_maxrss 的历史峰值），用于证明流式验证不随数据量涨内存。
"""

import os
import resource
import time

from . import gen
from .verify import Verifier, build_report


def _peak_rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _current_rss_mb():
    try:
        with open("/proc/self/status", "r", encoding="ascii") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    return _peak_rss_mb()


def run_perf(count=1_000_000, scenario="additive_fields", directory=None):
    """生成 count 条新版本数据并跑一次旧版本读取验证。"""
    if directory is None:
        import tempfile
        directory = tempfile.mkdtemp(prefix="rollback_perf_")
        cleanup = True
    else:
        cleanup = False

    t0 = time.perf_counter()
    data_path, _ = gen.write_scenario(directory, scenario, count, with_index=False)
    gen_sec = time.perf_counter() - t0
    file_mb = os.path.getsize(data_path) / (1024.0 * 1024.0)

    rss_before = _current_rss_mb()
    verifier = Verifier()
    t0 = time.perf_counter()
    stats = verifier.verify_files(data_path)
    verify_sec = time.perf_counter() - t0
    rss_after = _current_rss_mb()
    rss_peak = _peak_rss_mb()

    report = build_report(scenario, stats)
    result = {
        "scenario": scenario,
        "count": count,
        "data_file_mb": round(file_mb, 1),
        "generate_elapsed_sec": round(gen_sec, 3),
        "verify_elapsed_sec": round(verify_sec, 3),
        "records_per_sec": round(count / verify_sec),
        "peak_rss_mb": round(rss_peak, 1),
        "verify_rss_before_mb": round(rss_before, 1),
        "verify_rss_after_mb": round(rss_after, 1),
        "verify_rss_delta_mb": round(rss_after - rss_before, 1),
        "streaming": True,
        "conclusion": report["conclusion"],
        "read_ok": report["read_ok"],
        "read_failed": report["read_failed"],
    }

    if cleanup:
        os.remove(data_path)
        try:
            os.rmdir(directory)
        except OSError:
            pass
    return result
