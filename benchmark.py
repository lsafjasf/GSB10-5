"""基准：10 个并发实例进程 x 10,000 文件 = 100,000 个临时文件。

流程：并发创建 -> 全部 SIGKILL（制造残留）-> 单进程回收，记录耗时与内存峰值。
运行：python3 benchmark.py
"""
import json
import os
import resource
import signal
import subprocess
import sys
import tempfile
import time
import tracemalloc

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
from tmpfile_lib import reclaim  # noqa: E402

APP = "benchapp"
N_INSTANCES = 10
FILES_PER_INSTANCE = 10_000

WORKER = r"""
import os, sys, time
sys.path.insert(0, sys.argv[1])
from tmpfile_lib import TempInstance
inst = TempInstance(sys.argv[2], sys.argv[3])
payload = b"x" * 256
for _ in range(int(sys.argv[4])):
    p, fd = inst.new_temp("bench")
    os.write(fd, payload)
    os.close(fd)
print(inst.dir, flush=True)
time.sleep(600)
"""


def main():
    root = tempfile.mkdtemp(prefix="tmpfilelib-bench-")
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
    total = N_INSTANCES * FILES_PER_INSTANCE
    print(f"tmp_root={root}")
    print(f"instances={N_INSTANCES} files_per_instance={FILES_PER_INSTANCE} "
          f"total_files={total}")

    t0 = time.perf_counter()
    procs = [subprocess.Popen(
        [sys.executable, "-c", WORKER, src, APP, root,
         str(FILES_PER_INSTANCE)],
        stdout=subprocess.PIPE, text=True)
        for _ in range(N_INSTANCES)]
    dirs = [p.stdout.readline().strip() for p in procs]
    t_create = time.perf_counter() - t0
    print(f"[create] 10 processes x 10k files concurrently: "
          f"{t_create:.2f}s ({total / t_create:,.0f} files/s)")

    for p in procs:
        p.send_signal(signal.SIGKILL)
    for p in procs:
        p.wait()
    print(f"[kill] all {N_INSTANCES} instances SIGKILLed, residue left on disk")

    tracemalloc.start()
    t0 = time.perf_counter()
    report = reclaim(APP, root, grace_seconds=0.0)
    t_reclaim = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    maxrss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

    n_left = sum(len(files) for _, _, files in os.walk(
        os.path.join(root, APP))) if os.path.exists(
            os.path.join(root, APP)) else 0

    result = {
        "total_files": total,
        "create_seconds": round(t_create, 3),
        "create_files_per_sec": round(total / t_create),
        "reclaim_seconds": round(t_reclaim, 3),
        "reclaim_files_per_sec": round(len(report.reclaimed_files) / t_reclaim),
        "reclaimed_files": len(report.reclaimed_files),
        "residue_instances": len(report.residue),
        "removed_dirs": len(report.removed_dirs),
        "files_left_on_disk": n_left,
        "python_alloc_peak_mb": round(peak / 1024 / 1024, 1),
        "process_maxrss_mb": round(maxrss_mb, 1),
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))

    import shutil
    shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
