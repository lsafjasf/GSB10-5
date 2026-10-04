"""生成拆分对拍数据（确定性）。

产物（写入 examples/ 目录）:
  expected.stdout / expected.stderr —— 期望的原始两个流
  merged.mox1                      —— 真实子进程运行得到的合并文件
  SHA256SUMS                       —— 两个期望流的 sha256

验证方法:
  python3 streammerge.py split -i examples/merged.mox1 \
      --stdout /tmp/o --stderr /tmp/e
  sha256sum -c examples/SHA256SUMS   # 先把期望流拷到 /tmp，见 STRATEGY.md
"""

import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import streammerge  # noqa: E402

CHILD_SCRIPT = r"""
import sys
out = sys.stdout.buffer
err = sys.stderr.buffer
# 1) 两流交替成块
for i in range(10):
    out.write(("stdout line %02d\n" % i).encode())
    err.write(("stderr line %02d\n" % i).encode())
    out.flush(); err.flush()
# 2) 无换行结尾
out.write(b"no-final-newline")
err.write(b"err-no-newline")
out.flush(); err.flush()
"""


def expected_streams():
    out = b"".join(b"stdout line %02d\n" % i for i in range(10)) + b"no-final-newline"
    err = b"".join(b"stderr line %02d\n" % i for i in range(10)) + b"err-no-newline"
    return out, err


def main():
    out, err = expected_streams()
    out_path = os.path.join(HERE, "expected.stdout")
    err_path = os.path.join(HERE, "expected.stderr")
    merged_path = os.path.join(HERE, "merged.mox1")
    with open(out_path, "wb") as fh:
        fh.write(out)
    with open(err_path, "wb") as fh:
        fh.write(err)
    result = streammerge.merge(
        [sys.executable, "-c", CHILD_SCRIPT],
        output=merged_path, chunk_size=16,  # 故意切小，展示分帧
    )
    sums = os.path.join(HERE, "SHA256SUMS")
    with open(sums, "w") as fh:
        for path in (out_path, err_path):
            digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
            fh.write(f"{digest}  {os.path.basename(path)}\n")
    print("written:")
    for p in (out_path, err_path, merged_path, sums):
        print(" ", p, os.path.getsize(p), "bytes")
    print("merge:", result)


if __name__ == "__main__":
    main()
