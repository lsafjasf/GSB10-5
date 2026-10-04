"""bench.py — 合并/拆分基准：默认 100 万行，报告耗时与内存峰值。

用法:
  python3 bench.py                 # 100 万行基准（合并+拆分+流式校验）
  python3 bench.py --lines 2000000
  python3 bench.py --child 1000000 # 作为子进程生成确定性输出（内部使用）

内存指标:
  peak_rss_kb       进程生命周期内峰值常驻内存（ru_maxrss），包含 Python 解释器。
  peak_py_bytes     tracemalloc 统计的 Python 对象峰值，证明分配量有界。
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import subprocess
import sys
import tempfile
import time
import tracemalloc

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import streammerge  # noqa: E402

BATCH = 20_000
LINE_TEMPLATE = "line %08d %s\n"


def _line_bytes(index: int, tag: str) -> bytes:
    return (LINE_TEMPLATE % (index, tag)).encode("ascii")


def run_child(total_lines: int) -> None:
    """确定性生成器：stdout/stderr 各 total_lines//2 行，按批交替写。"""
    out = sys.stdout.buffer
    err = sys.stderr.buffer
    out_count = total_lines - total_lines // 2
    err_count = total_lines // 2
    oi = ei = 0
    while oi < out_count or ei < err_count:
        if oi < out_count:
            end = min(oi + BATCH, out_count)
            out.write(b"".join(_line_bytes(i, "O") for i in range(oi, end)))
            out.flush()
            oi = end
        if ei < err_count:
            end = min(ei + BATCH, err_count)
            err.write(b"".join(_line_bytes(i, "E") for i in range(ei, end)))
            err.flush()
            ei = end


class _LineVerifier:
    """流式校验器：按行比对重建流与生成器输出，常驻内存 O(单行)。"""

    def __init__(self, tag: str, expected_lines: int) -> None:
        self.tag = tag
        self.expected_lines = expected_lines
        self.next_index = 0
        self.buf = bytearray()

    def feed(self, data: bytes) -> None:
        self.buf += data
        while True:
            nl = self.buf.find(b"\n")
            if nl < 0:
                return
            line = bytes(self.buf[:nl + 1])
            del self.buf[:nl + 1]
            expected = _line_bytes(self.next_index, self.tag)
            if line != expected:
                raise AssertionError(
                    f"stream {self.tag} line {self.next_index} mismatch: "
                    f"{line!r} != {expected!r}")
            self.next_index += 1

    def finish(self) -> None:
        if self.buf:
            raise AssertionError(f"stream {self.tag}: trailing bytes {self.buf!r}")
        if self.next_index != self.expected_lines:
            raise AssertionError(
                f"stream {self.tag}: got {self.next_index} lines, "
                f"expected {self.expected_lines}")


def _verify_split(merged_path: str, total_lines: int) -> None:
    out_v = _LineVerifier("O", total_lines - total_lines // 2)
    err_v = _LineVerifier("E", total_lines // 2)
    with open(merged_path, "rb") as fh:
        for tag, payload in streammerge.frames_iter(fh):
            if tag == streammerge.TAG_STDOUT:
                out_v.feed(payload)
            elif tag == streammerge.TAG_STDERR:
                err_v.feed(payload)
    out_v.finish()
    err_v.finish()


def run_bench(total_lines: int, chunk_size: int) -> dict:
    child = [sys.executable, os.path.abspath(__file__), "--child",
             str(total_lines)]
    tracemalloc.start()
    tracemalloc.reset_peak()
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="mox1-bench-") as tmp:
        merged_path = os.path.join(tmp, "merged.mox1")
        result = streammerge.merge(child, output=merged_path,
                                   chunk_size=chunk_size)
        t1 = time.perf_counter()
        merged_size = os.path.getsize(merged_path)
        _verify_split(merged_path, total_lines)
        t2 = time.perf_counter()
    _cur, peak_py = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    peak_rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    payload = result.stdout_bytes + result.stderr_bytes
    report = {
        "lines": total_lines,
        "chunk_size": chunk_size,
        "payload_bytes": payload,
        "merged_bytes": merged_size,
        "frames": result.frames,
        "returncode": result.returncode,
        "merge_seconds": round(t1 - t0, 4),
        "split_verify_seconds": round(t2 - t1, 4),
        "merge_MBps": round(payload / (t1 - t0) / 1e6, 1),
        "peak_rss_kb": peak_rss_kb,
        "peak_py_bytes": peak_py,
        "verified": True,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="streammerge 基准")
    parser.add_argument("--lines", type=int, default=1_000_000)
    parser.add_argument("--chunk-size", type=int,
                        default=streammerge.DEFAULT_CHUNK_SIZE)
    parser.add_argument("--child", type=int, default=None,
                        help="内部参数：作为生成器子进程运行")
    args = parser.parse_args()
    if args.child is not None:
        run_child(args.child)
        return 0
    report = run_bench(args.lines, args.chunk_size)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
