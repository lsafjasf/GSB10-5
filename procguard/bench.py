"""1000 次 启动->清理 基准：耗时分布 + 内存峰值（仅标准库）。

用法:
  python3 bench.py [N]            # 计时基准（默认 1000 次）
  python3 bench.py [N] --memory   # 内存基准（tracemalloc 有开销，单独跑）
"""
import os
import resource
import statistics
import sys
import time
import tracemalloc

from procguard import ProcessTree, become_subreaper, list_pgroup, pgroup_exists

N = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 1000
MEM_MODE = "--memory" in sys.argv


def peak_rss_mb():
    # Linux: ru_maxrss 单位为 KB
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def main():
    become_subreaper()
    if MEM_MODE:
        tracemalloc.start()
    lat_ms = []
    pgids = []
    escalations = 0
    t0 = time.perf_counter()
    for _ in range(N):
        tree = ProcessTree(["sleep", "60"], term_timeout=1.0,
                           kill_timeout=1.0, poll_interval=0.005)
        tree.start()
        pgids.append(tree.pgid)
        s = time.perf_counter()
        r = tree.cleanup()
        lat_ms.append((time.perf_counter() - s) * 1000.0)
        escalations += int(r.escalated)
        if not r.completed:
            print("FAIL: leftover in pgid %d" % tree.pgid, file=sys.stderr)
            sys.exit(1)
    total = time.perf_counter() - t0
    py_peak = 0
    if MEM_MODE:
        _, py_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

    # 零遗留校验（双重）：
    # 1) 对每个 pgid 做 killpg(,0) 探针；2) 全量 /proc 扫描交叉核对
    probe_left = [g for g in pgids if pgroup_exists(g)]
    pgid_set = set(pgids)
    scan_left = [p for p in _scan_all() if p.pgid in pgid_set]

    lat_ms.sort()
    q = lambda p: lat_ms[min(N - 1, int(p * N))]
    print("模式:                %s" % ("内存(tracemalloc)" if MEM_MODE else "计时"))
    print("迭代次数:            %d" % N)
    print("总耗时:              %.3f s" % total)
    print("吞吐:                %.1f 次/s" % (N / total))
    print("单次清理 mean:       %.3f ms" % statistics.fmean(lat_ms))
    print("单次清理 p50/p95/p99: %.3f / %.3f / %.3f ms"
          % (q(0.50), q(0.95), q(0.99)))
    print("单次清理 min/max:    %.3f / %.3f ms" % (lat_ms[0], lat_ms[-1]))
    print("SIGKILL 兜底次数:    %d" % escalations)
    if MEM_MODE:
        print("Python 堆峰值:       %.2f MB (tracemalloc)" % (py_peak / 1e6))
    print("进程 RSS 峰值:       %.2f MB (ru_maxrss)" % peak_rss_mb())
    print("遗留(探针校验):      %s" % (probe_left or "无"))
    print("遗留(/proc 扫描):    %s" % ([p.pid for p in scan_left] or "无"))
    if probe_left or scan_left:
        sys.exit(1)
    print("结论:                %d/%d 全部清理干净" % (N, N))


def _scan_all():
    from procguard import _parse_stat
    out = []
    for name in os.listdir("/proc"):
        if name.isdigit():
            info = _parse_stat("/proc/%s/stat" % name)
            if info is not None:
                out.append(info)
    return out


if __name__ == "__main__":
    main()
