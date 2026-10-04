"""场景演示：命名样例 + 混合实例（存活/强杀残留/使用中）的回收校验输出。"""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
from tmpfile_lib import TempInstance, reclaim  # noqa: E402

APP = "myapp"
ROOT = tempfile.mkdtemp(prefix="tmpfilelib-demo-")
SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")

CHILD = r"""
import os, sys, time
sys.path.insert(0, sys.argv[1])
from tmpfile_lib import TempInstance
inst = TempInstance(sys.argv[2], sys.argv[3])
n = int(sys.argv[4])
for _ in range(n):
    p, fd = inst.new_temp(sys.argv[5])
    os.write(fd, b"payload")
    os.close(fd)
print(inst.dir, flush=True)
time.sleep(600)
"""

HOLDER = r"""
import os, sys, time
fd = os.open(sys.argv[1], os.O_RDONLY)
print("held", flush=True)
time.sleep(600)
"""


def spawn(kind, purpose, nfiles):
    p = subprocess.Popen([sys.executable, "-c", CHILD, SRC, APP, ROOT,
                          str(nfiles), purpose],
                         stdout=subprocess.PIPE, text=True)
    d = p.stdout.readline().strip()
    return p, d


def main():
    live = TempInstance(APP, ROOT)
    sample_path, fd = live.new_temp("upload")
    os.write(fd, b"in-progress")
    print("== 命名样例 ==")
    print(f"实例目录 : {live.dir}")
    print(f"数据文件 : {sample_path}")
    os.close(fd)

    p1, d1 = spawn("dead", "render", 3)
    p2, d2 = spawn("dead", "export", 2)
    p1.send_signal(signal.SIGKILL)
    p2.send_signal(signal.SIGKILL)
    p1.wait(); p2.wait()

    pinned = sorted(os.path.join(d2, n) for n in os.listdir(d2)
                    if n.startswith("export-"))[0]
    holder = subprocess.Popen([sys.executable, "-c", HOLDER, pinned],
                              stdout=subprocess.PIPE, text=True)
    holder.stdout.readline()

    print("\n== 回收前残留清单（kill -9 遗留）==")
    for d in (d1, d2):
        meta = json.load(open(os.path.join(d, "owner.json")))
        files = sorted(n for n in os.listdir(d) if n.endswith(".tmp"))
        print(f"  {d}")
        print(f"    pid={meta['pid']} start={meta['start_utc']} "
              f"files={len(files)}")
        for n in files:
            mark = "  <- 正被进程持有" if os.path.join(d, n) == pinned else ""
            print(f"    - {n}{mark}")

    report = reclaim(APP, ROOT, grace_seconds=0.0)

    print("\n== 回收校验数据 ==")
    print(f"扫描实例目录      : {report.scanned}")
    print(f"跳过-活实例(锁)   : {len(report.active_skipped)} "
          f"-> {[x['instance_id'] for x in report.active_skipped]}")
    print(f"识别残留实例      : {len(report.residue)} "
          f"-> {[x['instance_id'] for x in report.residue]}")
    print(f"回收文件数        : {len(report.reclaimed_files)}")
    print(f"跳过-使用中(句柄) : {len(report.in_use_skipped)} "
          f"-> {[x['path'] for x in report.in_use_skipped]}")
    print(f"删除实例目录      : {len(report.removed_dirs)}")

    live_files = [n for n in os.listdir(live.dir) if n.endswith(".tmp")]
    print(f"\n活实例文件完好    : {live_files}")
    print(f"使用中文件仍存在  : {os.path.exists(pinned)}")

    holder.send_signal(signal.SIGKILL); holder.wait()
    report2 = reclaim(APP, ROOT, grace_seconds=0.0)
    print(f"句柄释放后二次回收: 回收 {len(report2.reclaimed_files)} 个, "
          f"残留目录已清空 -> {sorted(os.listdir(os.path.join(ROOT, APP)))}")

    live.cleanup()
    shutil.rmtree(ROOT, ignore_errors=True)


if __name__ == "__main__":
    main()
