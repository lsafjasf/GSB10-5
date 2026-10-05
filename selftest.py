"""selftest.py - tmpfile_gc 自测与基准。运行: python3 selftest.py [root_dir]"""
import json
import multiprocessing as mp
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tmpfile_gc import TempFileManager, parse_name

PY = sys.executable
LIB = str(Path(__file__).resolve().parent)


def make_old(path, seconds):
    old = time.time() - seconds
    os.utime(path, (old, old))


def test_single_instance(root):
    print("== 用例1 单实例：只收自己过期的 ==")
    mgr = TempFileManager(root, "instA", min_age=5, heartbeat_ttl=10)
    fresh = mgr.new_file("cache"); fresh.write(b"x"); fresh.close()
    old1 = mgr.new_file("cache"); old1.close(); make_old(mgr.dir / os.path.basename(old1.name), 60)
    old2 = mgr.new_file("upload"); old2.close(); make_old(mgr.dir / os.path.basename(old2.name), 60)
    foreign = mgr.dir / "cache--instB--999--000001--deadbeef.tmp"
    foreign.write_text("x"); make_old(foreign, 60)  # 伪造的别人文件混进我的目录
    stats = mgr.sweep()
    ok = (stats["reclaimed"] == 2 and stats["skipped_young"] == 1
          and stats["skipped_foreign"] == 1 and foreign.exists()
          and os.path.exists(fresh.name))
    print("  stats:", stats, "=> PASS" if ok else "=> FAIL")
    mgr.close()
    return ok


def _concurrent_worker(root, idx, n_files, queue):
    mgr = TempFileManager(root, f"inst{idx:02d}", min_age=5, heartbeat_ttl=10)
    keep = []
    for i in range(n_files):
        fh = mgr.new_file("job")
        fh.write(b"data")
        fh.close()
        if i % 2 == 0:
            make_old(mgr.dir / os.path.basename(fh.name), 60)  # 一半过期
        else:
            keep.append(fh.name)                                # 一半必须存活
    stats = mgr.sweep()
    TempFileManager.reap_orphans(root, heartbeat_ttl=10, min_age=5,
                                 exclude_instance=mgr.instance_id)
    missing = [p for p in keep if not os.path.exists(p)]
    queue.put({"idx": idx, "stats": stats, "lost": len(missing)})
    mgr.close()


def test_concurrent(root):
    print("== 用例2 多实例并发（10 实例 x 200 文件，并发清理）==")
    queue = mp.Queue()
    procs = [mp.Process(target=_concurrent_worker, args=(root, i, 200, queue))
             for i in range(10)]
    t0 = time.perf_counter()
    for p in procs: p.start()
    for p in procs: p.join()
    dt = time.perf_counter() - t0
    results = [queue.get() for _ in procs]
    reclaimed = sum(r["stats"]["reclaimed"] for r in results)
    foreign = sum(r["stats"]["skipped_foreign"] for r in results)
    lost = sum(r["lost"] for r in results)
    ok = lost == 0 and reclaimed == 10 * 100
    print(f"  并发回收校验: 实例=10 每实例文件=200(100过期/100在用) "
          f"回收={reclaimed} 越界匹配={foreign} 误删在用文件={lost} 耗时={dt:.2f}s")
    print("  => PASS" if ok else "  => FAIL")
    return ok


def _victim_script(root):
    return (
        "import sys,time;sys.path.insert(0,%r);" % LIB +
        "from tmpfile_gc import TempFileManager;"
        "m=TempFileManager(%r,'victim',min_age=0,heartbeat_ttl=1.0,heartbeat_interval=0.3);" % root +
        "fs=[m.new_file('export') for _ in range(5)];"
        "[f.write(b'x') for f in fs];[f.close() for f in fs];"
        "print('READY',flush=True);time.sleep(60)")


def test_orphan_residue(root):
    print("== 用例3 强杀残留：识别并回收 ==")
    victim = subprocess.Popen([PY, "-c", _victim_script(root)],
                              stdout=subprocess.PIPE, text=True)
    victim.stdout.readline()  # READY
    time.sleep(0.5)
    victim.kill()             # SIGKILL，心跳线程随进程死亡
    victim.wait()
    time.sleep(1.5)           # 等心跳过期(ttl=1.0)
    vdir = Path(root) / "victim"
    residue = sorted(p.name for p in vdir.glob("*.tmp"))
    print(f"  残留清单({len(residue)}个):")
    for name in residue:
        print("   -", name)
    stats = TempFileManager.reap_orphans(root, heartbeat_ttl=1.0, min_age=0)
    left = list(vdir.glob("*.tmp")) if vdir.exists() else []
    ok = stats["reclaimed"] == 5 and not left and not vdir.exists()
    print("  回收结果:", stats, "目录已删除" if not vdir.exists() else "目录仍在")
    print("  => PASS" if ok else "  => FAIL")
    return ok


def _holder_script(path):
    return (
        "import fcntl,sys;"
        "fh=open(%r,'r+b');fcntl.flock(fh.fileno(),fcntl.LOCK_EX);" % path +
        "print('HOLDING',flush=True);"
        "sys.stdout.flush();"
        "import time;time.sleep(30)")


def test_in_use(root):
    print("== 用例4 文件正在被使用：不得回收（flock 占用）==")
    # 构造一个“已死亡实例”的过期文件，但另一进程正持有它的排他锁
    dead_dir = Path(root) / "deadinst"
    dead_dir.mkdir(parents=True, exist_ok=True)
    (dead_dir / "heartbeat.json").write_text(json.dumps(
        {"instance_id": "deadinst", "pid": 99999999, "pid_start": None, "ts": 0}))
    make_old(dead_dir / "heartbeat.json", 3600)
    target = dead_dir / "export--deadinst--1234--000001--abcd1234.tmp"
    target.write_text("hot")
    make_old(target, 3600)
    holder = subprocess.Popen([PY, "-u", "-c", _holder_script(str(target))],
                              stdout=subprocess.PIPE, text=True)
    holder.stdout.readline()  # HOLDING
    stats1 = TempFileManager.reap_orphans(root, heartbeat_ttl=1.0, min_age=0)
    still_there = target.exists()
    holder.terminate(); holder.wait()
    time.sleep(0.2)
    stats2 = TempFileManager.reap_orphans(root, heartbeat_ttl=1.0, min_age=0)
    gone = not target.exists()
    ok = (stats1["skipped_in_use"] == 1 and stats1["reclaimed"] == 0
          and still_there and stats2["reclaimed"] == 1 and gone)
    print(f"  持锁期间: reclaimed={stats1['reclaimed']} skipped_in_use={stats1['skipped_in_use']} 文件仍在={still_there}")
    print(f"  释放锁后: reclaimed={stats2['reclaimed']} 文件已删={gone}")
    print("  => PASS" if ok else "  => FAIL")
    return ok



def _reaper_worker(root, queue):
    stats = TempFileManager.reap_orphans(root, heartbeat_ttl=1.0, min_age=0)
    queue.put(stats)


def test_concurrent_reapers(root):
    print("== 用例5 多个回收者并发扫同一批残留（竞争安全）==")
    for d in range(5):
        ddir = Path(root) / f"ghost{d}"
        ddir.mkdir(parents=True, exist_ok=True)
        (ddir / "heartbeat.json").write_text(json.dumps(
            {"instance_id": f"ghost{d}", "pid": 999000 + d, "pid_start": None, "ts": 0}))
        make_old(ddir / "heartbeat.json", 3600)
        for i in range(1000):
            p = ddir / f"cache--ghost{d}--{999000+d}--{i:06d}--{i:08x}.tmp"
            p.write_text("x"); make_old(p, 3600)
    queue = mp.Queue()
    procs = [mp.Process(target=_reaper_worker, args=(root, queue)) for _ in range(5)]
    t0 = time.perf_counter()
    for p in procs: p.start()
    for p in procs: p.join()
    dt = time.perf_counter() - t0
    stats = [queue.get() for _ in procs]
    reclaimed = sum(s["reclaimed"] for s in stats)
    raced = sum(s["raced"] for s in stats)
    left = len(list(Path(root).rglob("*.tmp")))
    ok = reclaimed == 5000 and left == 0
    print(f"  残留=5000 并发回收者=5 总回收={reclaimed} 竞争让位(raced)={raced} "
          f"实际剩余={left} 耗时={dt:.2f}s")
    print("  => PASS" if ok else "  => FAIL")
    return ok

def _bench_worker(root, idx, n_files, queue):
    mgr = TempFileManager(root, f"bench{idx:02d}", min_age=0, heartbeat_ttl=10)
    for _ in range(n_files):
        fh = mgr.new_file("bulk")
        fh.close()
        make_old(mgr.dir / os.path.basename(fh.name), 60)
    t0 = time.perf_counter()
    stats = mgr.sweep()
    dt = time.perf_counter() - t0
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    queue.put({"idx": idx, "reclaimed": stats["reclaimed"], "sweep_s": dt, "peak_kb": peak})
    mgr.close()


def benchmark(root):
    print("== 基准 10 万文件 / 10 并发实例 ==")
    n_inst, n_files = 10, 10_000
    queue = mp.Queue()
    procs = [mp.Process(target=_bench_worker, args=(root, i, n_files, queue))
             for i in range(n_inst)]
    t0 = time.perf_counter()
    for p in procs: p.start()
    for p in procs: p.join()
    total = time.perf_counter() - t0
    results = [queue.get() for _ in procs]
    reclaimed = sum(r["reclaimed"] for r in results)
    sweep_max = max(r["sweep_s"] for r in results)
    peak_kb = max(r["peak_kb"] for r in results)
    child_peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    leftover_names = sorted(str(p.relative_to(root)) for p in Path(root).rglob("*.tmp"))[:10]
    leftovers = len(list(Path(root).rglob("*.tmp")))
    print(f"  文件总数={n_inst*n_files} 实例={n_inst} 每实例={n_files}")
    print(f"  总回收={reclaimed} 残留={leftovers} 残留样例={leftover_names}")
    print(f"  端到端耗时(建文件+并发回收)={total:.2f}s  单实例sweep最大={sweep_max:.2f}s")
    print(f"  内存峰值 ru_maxrss: 子进程最大={peak_kb/1024:.1f}MiB RUSAGE_CHILDREN={child_peak/1024:.1f}MiB")
    ok = reclaimed == n_inst * n_files and leftovers == 0
    print("  => PASS" if ok else "  => FAIL")
    return ok


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="gsb1tmp-")
    print(f"临时根目录: {root}\n命名样例: "
          f"{Path(root)}/web01-p4210-20261006T021530Z-a3f9/"
          f"upload--web01-p4210-20261006T021530Z-a3f9--4210--000017--9c2bd4e1.tmp\n")
    cases = [
        ("single_instance", test_single_instance),
        ("concurrent_10", test_concurrent),
        ("orphan_residue", test_orphan_residue),
        ("in_use_protected", test_in_use),
        ("concurrent_reapers", test_concurrent_reapers),
        ("benchmark_100k", benchmark),
    ]
    results = {}
    for name, fn in cases:
        sub = Path(root) / name
        sub.mkdir(parents=True, exist_ok=True)
        results[name] = fn(str(sub))
    print("\n== 汇总 ==")
    for name, ok in results.items():
        print(f"  {name}: {'PASS' if ok else 'FAIL'}")
    shutil.rmtree(root, ignore_errors=True)
    sys.exit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
