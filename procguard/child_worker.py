"""被 procguard 监管的测试用子进程：多层派生 / 忽略信号等场景。

用法:
  child_worker.py sleep
  child_worker.py tree <depth> <fanout> <pidsfile>
  child_worker.py ignore <pidsfile>     # 自身与后代都忽略 TERM/INT/HUP/QUIT
"""
import os
import signal
import sys
import time


def write_pid(path):
    with open(path, "a") as f:
        f.write("%d\n" % os.getpid())


def main():
    mode = sys.argv[1]
    if mode == "sleep":
        while True:
            time.sleep(60)
    if mode == "tree":
        depth, fanout, pidsfile = int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
        write_pid(pidsfile)
        children = []
        if depth > 0:
            for _ in range(fanout):
                children.append(os.fork())
                if children[-1] == 0:
                    os.execv(sys.executable,
                             [sys.executable, sys.argv[0], "tree",
                              str(depth - 1), str(fanout), pidsfile])
        # 所有层都驻留，模拟真实服务派生的 worker
        while True:
            pid, _ = os.wait()
            children = [p for p in children if p != pid]
    if mode == "ignore":
        pidsfile, depth = sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 1
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT):
            signal.signal(sig, signal.SIG_IGN)
        write_pid(pidsfile)
        if depth > 0:
            child = os.fork()
            if child == 0:
                os.execv(sys.executable,
                         [sys.executable, sys.argv[0], "ignore",
                          pidsfile, str(depth - 1)])
            while True:
                os.waitpid(child, 0)
        else:
            while True:
                time.sleep(60)
    sys.exit(2)


if __name__ == "__main__":
    main()
