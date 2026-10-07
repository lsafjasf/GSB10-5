"""端到端演示：supervisor 收到外部 SIGTERM -> 转发整个多层进程组 -> 清理退出。"""
import os
import signal
import sys
import time

from procguard import (
    ProcessTree,
    become_subreaper,
    list_pgroup,
    pid_alive,
)

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    become_subreaper()
    pidsfile = sys.argv[1]
    tree = ProcessTree(
        [sys.executable, os.path.join(HERE, "child_worker.py"),
         "tree", "2", "2", pidsfile],
        term_timeout=2.0, poll_interval=0.01,
    )
    pgid = tree.start()
    tree.install_signal_handlers()
    print("supervisor pid=%d, 子进程组 pgid=%d（子进程为会话首进程/组长）"
          % (os.getpid(), pgid), flush=True)

    while tree.last_result is None:
        time.sleep(0.05)

    pids = [int(x) for x in open(pidsfile).read().split()]
    print("转发信号:          SIGTERM 经 killpg(%d) 投递到整组" % pgid)
    print("清理结果:          " + tree.last_result.summary())
    time.sleep(0.05)
    leftovers = [p for p in pids if pid_alive(p, allow_zombie=True)]
    print("启动的全部 pid:    %s" % sorted(pids))
    print("清理后存活清单:    %s" % (leftovers or "无"))
    print("/proc 组内扫描:    %s" %
          ([p.pid for p in list_pgroup(pgid)] or "空"))
    tree.restore_signal_handlers()
    sys.exit(0)


if __name__ == "__main__":
    main()
