"""多层派生辅助进程：递归派生 depth 层子孙后常驻。"""
import os
import subprocess
import sys
import time

depth = int(sys.argv[1])
if depth > 0:
    subprocess.Popen([sys.executable, os.path.abspath(__file__), str(depth - 1)])
time.sleep(3600)
