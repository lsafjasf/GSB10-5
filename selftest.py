"""自测 + 基准。运行: python3 selftest.py
产物写入 out/：引用图、处理结果、校验数据、耗时与内存数据。
"""
import json
import os
import random
import resource
import sys
import time
import tracemalloc

import volref
from volref import VolumeRegistry, garbage_collect, verify, PERSISTENT, TEMPORARY

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")

NOW = 1_000.0


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  PASS {msg}")


# ---------- 场景测试 ----------

def t_no_reference():
    reg = VolumeRegistry()
    reg.create_volume("v-free")
    rep = garbage_collect(reg, NOW)
    check(rep.reclaimed == ["v-free"], "无引用卷被回收")
    check(verify(reg, rep, NOW)["ok"], "校验通过（无引用场景）")


def t_single_reference():
    reg = VolumeRegistry()
    reg.create_volume("v1")
    reg.create_container("c1")
    reg.add_ref("c1", "v1")
    rep = garbage_collect(reg, NOW)
    check(rep.retained == ["v1"] and rep.reclaimed == [], "被单个容器引用的卷保留")
    check(rep.referrers_of["v1"] == ["c1"], "引用者可列出")
    check(verify(reg, rep, NOW)["ok"], "校验通过（单引用场景）")


def t_multi_reference():
    reg = VolumeRegistry()
    reg.create_volume("v1")
    for c in ("c1", "c2", "c3"):
        reg.create_container(c)
        reg.add_ref(c, "v1")
    reg.remove_container("c2")          # 一个引用者退出
    rep = garbage_collect(reg, NOW)
    check(rep.retained == ["v1"], "多引用卷在部分引用者退出后仍保留")
    check(rep.referrers_of["v1"] == ["c1", "c3"], "剩余引用者正确")
    check(verify(reg, rep, NOW)["ok"], "校验通过（多引用场景-部分退出）")
    reg.remove_container("c1")
    reg.remove_container("c3")
    rep2 = garbage_collect(reg, NOW)
    check(rep2.reclaimed == ["v1"], "全部引用者退出后卷被回收")
    check(verify(reg, rep2, NOW)["ok"], "校验通过（多引用场景-全部退出）")


def t_dangling_reference():
    reg = VolumeRegistry()
    reg.create_volume("v-orphan")
    reg.create_container("ghost")
    reg.add_ref("ghost", "v-orphan")
    reg.remove_container("ghost")       # 引用者删除 -> 引用悬空
    # 模拟外部残留：手工把悬空记录塞回索引（如快照恢复出的脏数据）
    from volref import Reference
    ref = Reference("ghost", "v-orphan")
    reg.restore_ref_record(ref)
    check(reg.dangling_references() == [ref], "悬空引用可被检测")
    check(not reg.is_referenced("v-orphan", NOW), "悬空引用不算有效引用")
    rep = garbage_collect(reg, NOW)
    check(rep.pruned_dangling == [ref], "悬空引用被确定地清理")
    check(rep.reclaimed == ["v-orphan"], "仅被悬空引用的卷被回收")
    check(verify(reg, rep, NOW)["ok"], "校验通过（悬空引用场景）")


def t_temporary_mount():
    reg = VolumeRegistry()
    reg.create_volume("v-tmp-live")
    reg.create_volume("v-tmp-dead")
    reg.create_container("c1")
    reg.add_ref("c1", "v-tmp-live", kind=TEMPORARY, created_at=900.0, expires_at=NOW + 60)
    reg.add_ref("c1", "v-tmp-dead", kind=TEMPORARY, created_at=800.0, expires_at=NOW - 1)
    rep = garbage_collect(reg, NOW)
    check(rep.retained == ["v-tmp-live"], "未过期临时挂载保护卷不被回收")
    check(rep.reclaimed == ["v-tmp-dead"], "过期临时挂载不保护卷，卷被回收")
    check(len(rep.pruned_expired) == 1, "过期临时挂载被确定地清理")
    check(verify(reg, rep, NOW)["ok"], "校验通过（临时挂载场景）")


def t_reference_graph_export():
    reg = VolumeRegistry()
    for v in ("v1", "v2", "v3"):
        reg.create_volume(v)
    for c in ("c1", "c2"):
        reg.create_container(c)
    reg.add_ref("c1", "v1")
    reg.add_ref("c2", "v1")
    reg.add_ref("c2", "v2")
    g = reg.reference_graph()
    check(g == {"v1": ["c1", "c2"], "v2": ["c2"], "v3": []}, "引用图数据正确")
    return g


# ---------- 基准 ----------

def benchmark(n_volumes=100_000, n_refs=500_000, seed=42):
    print(f"\n[benchmark] volumes={n_volumes} refs={n_refs}")
    rng = random.Random(seed)
    tracemalloc.start()
    t0 = time.perf_counter()

    reg = VolumeRegistry()
    for i in range(n_volumes):
        reg.create_volume(f"vol-{i}")
    n_containers = n_refs // 5
    for i in range(n_containers):
        reg.create_container(f"ctr-{i}")

    # 引用分布：约 80% 的卷被 1~4 个容器引用，其余无引用
    referenced = rng.sample(range(n_volumes), int(n_volumes * 0.8))
    made = 0
    vi = 0
    while made < n_refs:
        v = referenced[vi % len(referenced)]
        vi += 1
        c = rng.randrange(n_containers)
        kind = TEMPORARY if rng.random() < 0.05 else PERSISTENT
        expires = (NOW + rng.uniform(10, 1000)) if kind == TEMPORARY else None
        try:
            reg.add_ref(f"ctr-{c}", f"vol-{v}", kind=kind, created_at=NOW, expires_at=expires)
            made += 1
        except Exception:
            continue

    actual_refs = sum(len(m) for m in reg.refs_by_referrer.values())

    # 删除 10% 的容器 -> 产生悬空引用（remove_container 即时摘除，属于正常路径）
    for i in range(0, n_containers, 10):
        reg.remove_container(f"ctr-{i}")

    t_build = time.perf_counter() - t0

    t1 = time.perf_counter()
    rep = garbage_collect(reg, NOW)
    t_gc = time.perf_counter() - t1

    t2 = time.perf_counter()
    ver = verify(reg, rep, NOW)
    t_verify = time.perf_counter() - t2

    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0

    result = {
        "volumes": n_volumes,
        "references_requested": n_refs,
        "references_actual_unique": actual_refs,
        "build_seconds": round(t_build, 3),
        "gc_seconds": round(t_gc, 3),
        "verify_seconds": round(t_verify, 3),
        "total_decision_seconds": round(t_gc + t_verify, 3),
        "peak_python_bytes_tracemalloc": peak,
        "peak_python_mb": round(peak / 1024 / 1024, 1),
        "max_rss_mb": round(rss_mb, 1),
        "reclaimed": len(rep.reclaimed),
        "retained": len(rep.retained),
        "verify_ok": ver["ok"],
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return rep, ver, result


def main():
    os.makedirs(OUT, exist_ok=True)
    print("[scenario tests]")
    t_no_reference()
    t_single_reference()
    t_multi_reference()
    t_dangling_reference()
    t_temporary_mount()
    graph = t_reference_graph_export()

    # 小规模端到端：导出引用图、处理结果、校验数据
    reg = VolumeRegistry()
    for v in ("data-a", "data-b", "data-c", "cache-x"):
        reg.create_volume(v)
    for c in ("web-1", "web-2", "worker-1"):
        reg.create_container(c)
    reg.add_ref("web-1", "data-a")
    reg.add_ref("web-2", "data-a")
    reg.add_ref("worker-1", "data-b")
    reg.add_ref("web-1", "cache-x", kind=TEMPORARY, created_at=NOW - 100, expires_at=NOW - 1)
    reg.remove_container("worker-1")    # data-b 的唯一引用者退出 -> 悬空
    # 模拟快照恢复带回的悬空记录：引用者 ghost-9 已不存在
    from volref import Reference
    reg.restore_ref_record(Reference("ghost-9", "data-c"))
    with open(os.path.join(OUT, "reference_graph.json"), "w") as f:
        json.dump(reg.raw_graph(NOW), f, indent=2, ensure_ascii=False)
    rep = garbage_collect(reg, NOW)
    ver = verify(reg, rep, NOW)
    with open(os.path.join(OUT, "gc_result.json"), "w") as f:
        json.dump(rep.to_dict(), f, indent=2, ensure_ascii=False)
    with open(os.path.join(OUT, "verification.json"), "w") as f:
        json.dump(ver, f, indent=2, ensure_ascii=False)
    check(ver["ok"], "端到端校验通过")

    rep_b, ver_b, bench = benchmark()
    with open(os.path.join(OUT, "benchmark.json"), "w") as f:
        json.dump(bench, f, indent=2, ensure_ascii=False)
    check(ver_b["ok"], "10 万卷 / 50 万引用 校验通过")
    print("\nALL TESTS PASSED")


if __name__ == "__main__":
    main()
