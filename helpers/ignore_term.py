"""忽略 SIGTERM 的顽固进程：安装 SIG_IGN 后写 ready 文件并常驻。"""
import signal
import sys
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
with open(sys.argv[1], "w") as fh:
    fh.write("ready")
time.sleep(3600)
