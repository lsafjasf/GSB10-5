"""benchmark.py — 规模压测：10 万个卷、50 万条引用。

运行：python3 benchmark.py
产出 out/benchmark.json：各阶段耗时、判定结果计数、内存峰值。

内存口径：
  - tracemalloc 峰值：Python 层由本库占用的堆内存（不含解释器基线）；
  - ru_maxrss：整个进程的驻留内存峰值（Linux 单位 KB，含解释器基线）。
"""

import json
import os
import random
import resource
import time
import tracemalloc

from volume_gc import VolumeGC

VOLUMES = 100_000
CONTAINERS = 60_000
REFS = 500_000
TEMP_RATIO = 0.05
DESTROYED_RATIO = 0.10
SEED = 42

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")


def main():
    rng = random.Random(SEED)
    tracemalloc.start()
    gc = VolumeGC()

    t0 = time.perf_counter()
    for i in range(VOLUMES):
        gc.add_volume(f"vol-{i:06d}")
    for i in range(CONTAINERS):
        gc.register_container(f"ctr-{i:06d}")
    t_topo = time.perf_counter() - t0

    # 50 万条引用，确定性生成；少量临时挂载
    t0 = time.perf_counter()
    temp_count = 0
    for i in range(REFS):
        cid = f"ctr-{rng.randrange(CONTAINERS):06d}"
        vid = f"vol-{rng.randrange(VOLUMES):06d}"
        temporary = rng.random() < TEMP_RATIO
        temp_count += int(temporary)
        gc.mount(cid, vid, f"/mnt/{i % 16}", temporary=temporary)
    t_mount = time.perf_counter() - t0

    # 销毁 10% 容器，制造悬空引用
    destroyed = rng.sample(range(CONTAINERS), int(CONTAINERS * DESTROYED_RATIO))
    t0 = time.perf_counter()
    for i in destroyed:
        gc.destroy_container(f"ctr-{i:06d}")
    t_destroy = time.perf_counter() - t0

    # 判定阶段：列出引用图 + 找出全部无有效引用的卷
    t0 = time.perf_counter()
    graph = gc.reference_graph()
    reclaimable = gc.reclaimable_volumes()
    t_decide = time.perf_counter() - t0

    referenced = VOLUMES - len(reclaimable)
    dangling = len(gc.dangling_refs())

    # 回收 + 校验
    t0 = time.perf_counter()
    result = gc.reclaim()
    t_reclaim = time.perf_counter() - t0

    t0 = time.perf_counter()
    report = gc.validate(result.reclaimed, result.kept)
    t_validate = time.perf_counter() - t0

    _, py_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_peak_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    data = {
        "scale": {"volumes": VOLUMES, "containers": CONTAINERS, "refs": REFS,
                  "seed": SEED,
                  "temporary_refs": temp_count,
                  "destroyed_containers": len(destroyed)},
        "timing_seconds": {
            "build_topology": round(t_topo, 4),
            "mount_500k_refs": round(t_mount, 4),
            "destroy_containers": round(t_destroy, 4),
            "decide_graph_and_reclaimable": round(t_decide, 4),
            "reclaim": round(t_reclaim, 4),
            "validate": round(t_validate, 4),
            "total_decide_plus_reclaim_plus_validate":
                round(t_decide + t_reclaim + t_validate, 4),
        },
        "decision": {
            "referenced_volumes": referenced,
            "reclaimable_volumes": len(reclaimable),
            "dangling_refs_seen": dangling,
        },
        "reclaim_result": {
            "reclaimed": len(result.reclaimed),
            "kept": len(result.kept),
            "pruned_dangling": len(result.pruned_dangling),
            "pruned_temporary": len(result.pruned_temporary),
        },
        "validation_passed": report["passed"],
        "memory_peak": {
            "tracemalloc_python_bytes": py_peak,
            "tracemalloc_python_mib": round(py_peak / 1024 / 1024, 2),
            "ru_maxrss_kib": rss_peak_kb,
            "ru_maxrss_mib": round(rss_peak_kb / 1024, 2),
        },
        "python_version": os.sys.version.split()[0],
        "platform": os.uname().sysname + " " + os.uname().machine,
    }

    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "benchmark.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
