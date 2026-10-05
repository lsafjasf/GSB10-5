"""收到 SIGTERM 时写标记文件再退出，用于验证信号确实被转发。"""
import signal
import sys
import time

marker, ready = sys.argv[1], sys.argv[2]

def handler(signum, frame):
    with open(marker, "w") as fh:
        fh.write(str(signum))
    sys.exit(0)

signal.signal(signal.SIGTERM, handler)
with open(ready, "w") as fh:
    fh.write("ready")
time.sleep(3600)
