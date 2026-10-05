"""demo.py — 构造一个覆盖全部情形的场景，产出交付数据文件到 out/。

运行：python3 demo.py
产出：
  out/reference_graph.json  引用图数据（卷 -> 有效引用者）
  out/edges.json            全部已记录边（含状态标注）
  out/reclaim_result.json   回收处理结果
  out/validation.json       回收后校验数据
"""

import json
import os

from volume_gc import VolumeGC

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")


def build_scenario() -> VolumeGC:
    gc = VolumeGC()
    for c in ("web-1", "web-2", "worker-1", "cron-1", "dead-1", "dead-2"):
        gc.register_container(c)
    for v in ("vol-orphan", "vol-single", "vol-shared", "vol-dangling",
              "vol-temp-only", "vol-mixed"):
        gc.add_volume(v)

    # 无引用：vol-orphan 不挂任何边
    # 单个引用
    gc.mount("web-1", "vol-single", "/var/lib/app")
    # 多个引用
    gc.mount("web-1", "vol-shared", "/shared")
    gc.mount("web-2", "vol-shared", "/shared")
    gc.mount("worker-1", "vol-shared", "/mnt/shared")
    # 悬空引用：挂载后销毁引用者
    gc.mount("dead-1", "vol-dangling", "/data")
    gc.mount("dead-2", "vol-dangling", "/data")
    gc.destroy_container("dead-1")
    gc.destroy_container("dead-2")
    # 仅临时挂载
    gc.mount("cron-1", "vol-temp-only", "/scratch", temporary=True)
    # 混合：临时 + 悬空 + 一个有效引用
    gc.mount("cron-1", "vol-mixed", "/scratch", temporary=True)
    gc.register_container("ghost-1")
    gc.mount("ghost-1", "vol-mixed", "/old")
    gc.destroy_container("ghost-1")
    gc.mount("web-2", "vol-mixed", "/data")
    return gc


def dump(name, obj):
    path = os.path.join(OUT, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
    print(f"wrote {path}")


def main():
    os.makedirs(OUT, exist_ok=True)
    gc = build_scenario()

    dump("edges.json", gc.full_edge_list())
    dump("reference_graph.json", gc.reference_graph())

    result = gc.reclaim()
    dump("reclaim_result.json", {
        "reclaimed": result.reclaimed,
        "kept": result.kept,
        "pruned_dangling": [r.key for r in result.pruned_dangling],
        "pruned_temporary": [r.key for r in result.pruned_temporary],
    })

    report = gc.validate(result.reclaimed, result.kept)
    dump("validation.json", report)
    print("validation passed:", report["passed"])


if __name__ == "__main__":
    main()
