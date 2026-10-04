"""streammerge.py

子进程 stdout / stderr 无损合并与拆分（Python 3，仅标准库）。

策略：带流标签的定长前缀分块（framed chunks），即"按块"合并。
- 每块上限 CHUNK_SIZE 字节，块首为 1 字节流标签 + 4 字节大端长度，
  再跟最多 CHUNK_SIZE 字节的原始数据。
- 合并文件以魔数 b"MOX1" 开头，结尾可选一个退出状态帧。
- 拆分时按帧逐块写回对应流，逐字节还原，零信息丢失。
- 内存常驻上界约为 CHUNK_SIZE（另加每块 5 字节帧头缓冲），
  与输出总量无关；无换行结尾的输出和超长行都被确定性处理。

用法见模块底部 main() 或运行: python3 streammerge.py --help
"""

from __future__ import annotations

import argparse
import json
import os
import selectors
import subprocess
import sys
from dataclasses import dataclass
from typing import BinaryIO, Iterator, Optional, Sequence, Tuple, Union

MAGIC = b"MOX1"
TAG_STDOUT = 1
TAG_STDERR = 2
TAG_EXIT = 3
DEFAULT_CHUNK_SIZE = 64 * 1024
_HEADER_LEN = 5


class FormatError(ValueError):
    """合并流格式损坏时抛出。"""


# --------------------------------------------------------------------------- #
# 帧写入 / 读取
# --------------------------------------------------------------------------- #
class FrameWriter:
    """把 (流标签, 字节块) 写成 MOX1 帧流。

    sink 可以是已打开的二进制文件对象，也可以是路径（以 'wb' 新建）。
    写入是流式的，本类不缓冲数据块以外的内容。
    """

    def __init__(self, sink: Union[BinaryIO, str, os.PathLike],
                 chunk_size: int = DEFAULT_CHUNK_SIZE) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        self.chunk_size = chunk_size
        if hasattr(sink, "write"):
            self._fh = sink
            self._owns = False
        else:
            self._fh = open(sink, "wb")
            self._owns = True
        self._fh.write(MAGIC)
        self.frames = 0
        self.bytes_out = 0

    def write_data(self, tag: int, data: bytes) -> None:
        if tag not in (TAG_STDOUT, TAG_STDERR):
            raise ValueError("data tag must be TAG_STDOUT or TAG_STDERR")
        view = memoryview(data)
        while len(view) > self.chunk_size:
            self._emit(tag, bytes(view[:self.chunk_size]))
            view = view[self.chunk_size:]
        if len(view):
            self._emit(tag, bytes(view))

    def _emit(self, tag: int, payload: bytes) -> None:
        self._fh.write(bytes([tag]) + len(payload).to_bytes(4, "big") + payload)
        self.frames += 1
        self.bytes_out += _HEADER_LEN + len(payload)

    def write_exit(self, returncode: int) -> None:
        # 非负：退出码；负：被信号 -returncode 终止。
        payload = returncode.to_bytes(4, "big", signed=True)
        self._fh.write(bytes([TAG_EXIT]) + len(payload).to_bytes(4, "big") + payload)
        self.frames += 1
        self.bytes_out += _HEADER_LEN + 1

    def close(self) -> None:
        if self._owns:
            self._fh.close()

    def __enter__(self) -> "FrameWriter":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def frames_iter(stream: BinaryIO) -> Iterator[Tuple[int, bytes]]:
    """逐帧解析 MOX1 流，产出 (标签, 负载)。流式读取，不缓存全文件。"""
    if stream.read(4) != MAGIC:
        raise FormatError("bad magic: not a MOX1 merged stream")
    while True:
        header = stream.read(_HEADER_LEN)
        if not header:
            return
        if len(header) < _HEADER_LEN:
            raise FormatError("truncated frame header")
        tag = header[0]
        length = int.from_bytes(header[1:5], "big")
        if tag not in (TAG_STDOUT, TAG_STDERR, TAG_EXIT):
            raise FormatError(f"unknown frame tag: {tag}")
        payload = stream.read(length) if length else b""
        if len(payload) != length:
            raise FormatError("truncated frame payload")
        yield tag, payload


def split(merged: Union[BinaryIO, str, os.PathLike],
          stdout_dst: Union[BinaryIO, str, os.PathLike],
          stderr_dst: Union[BinaryIO, str, os.PathLike]) -> int:
    """把合并流拆回 stdout / stderr，返回子进程 returncode。

    输出参数可为二进制文件对象或路径。逐帧写出，内存占用为单块上限。
    """
    def _open(dst: Union[BinaryIO, str, os.PathLike]) -> Tuple[BinaryIO, bool]:
        if hasattr(dst, "write"):
            return dst, False
        return open(dst, "wb"), True

    src = merged if hasattr(merged, "read") else open(merged, "rb")
    out_fh, out_owned = _open(stdout_dst)
    err_fh, err_owned = _open(stderr_dst)
    returncode = 0
    try:
        for tag, payload in frames_iter(src):
            if tag == TAG_STDOUT:
                out_fh.write(payload)
            elif tag == TAG_STDERR:
                err_fh.write(payload)
            else:
                if len(payload) != 4:
                    raise FormatError("bad exit frame")
                returncode = int.from_bytes(payload, "big", signed=True)
    finally:
        if out_owned:
            out_fh.close()
        if err_owned:
            err_fh.close()
        if not hasattr(merged, "read"):
            src.close()
    return returncode


# --------------------------------------------------------------------------- #
# 合并核心：两个只读二进制流 -> FrameWriter
# --------------------------------------------------------------------------- #
def merge_pipes(stdout_rd: BinaryIO, stderr_rd: BinaryIO,
                writer: FrameWriter) -> Tuple[int, int]:
    """用 selector 公平轮询两个已存在的管道读端并写入帧流。

    返回 (stdout 字节数, stderr 字节数)。EOF 后注销对应 fd。
    每个流最多一次 read1(<=chunk_size) 后回到 selector，
    保证两个流交替获得服务，不会让一个流长期独占。
    """
    sel = selectors.DefaultSelector()
    sel.register(stdout_rd, selectors.EVENT_READ, TAG_STDOUT)
    sel.register(stderr_rd, selectors.EVENT_READ, TAG_STDERR)
    readers = {TAG_STDOUT: stdout_rd, TAG_STDERR: stderr_rd}
    total = {TAG_STDOUT: 0, TAG_STDERR: 0}
    try:
        while sel.get_map():
            for key, _events in sel.select():
                tag = key.data
                chunk = readers[tag].read1(writer.chunk_size)
                if chunk:
                    writer.write_data(tag, chunk)
                    total[tag] += len(chunk)
                else:
                    sel.unregister(key.fileobj)
    finally:
        sel.close()
    return total[TAG_STDOUT], total[TAG_STDERR]


@dataclass
class MergeResult:
    returncode: int
    stdout_bytes: int
    stderr_bytes: int
    frames: int


def merge(argv: Sequence[str],
          output: Union[BinaryIO, str, os.PathLike] = "merged.mox1",
          chunk_size: int = DEFAULT_CHUNK_SIZE,
          cwd: Optional[str] = None,
          env: Optional[dict] = None) -> MergeResult:
    """运行 argv 子进程，把 stdout/stderr 无损合并进 output（MOX1 格式）。"""
    with FrameWriter(output, chunk_size=chunk_size) as writer:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=env,
        )
        try:
            out_n, err_n = merge_pipes(proc.stdout, proc.stderr, writer)
        finally:
            proc.stdout.close()
            proc.stderr.close()
            returncode = proc.wait()
        writer.write_exit(returncode)
    return MergeResult(returncode, out_n, err_n, writer.frames)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _cli(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="streammerge",
        description="子进程 stdout/stderr 无损合并与拆分（MOX1 格式）",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_merge = sub.add_parser("merge", help="运行命令并合并其两个输出流")
    p_merge.add_argument("-o", "--output", default="merged.mox1")
    p_merge.add_argument("-c", "--chunk-size", type=int,
                         default=DEFAULT_CHUNK_SIZE)
    p_merge.add_argument("command", nargs=argparse.REMAINDER)

    p_split = sub.add_parser("split", help="把 .mox1 拆回两个流")
    p_split.add_argument("-i", "--input", default="merged.mox1")
    p_split.add_argument("--stdout", default="split.stdout")
    p_split.add_argument("--stderr", default="split.stderr")

    args = parser.parse_args(argv)
    if args.cmd == "merge":
        cmd = args.command
        if cmd and cmd[0] == "--":
            cmd = cmd[1:]
        if not cmd:
            p_merge.error("missing command")
        result = merge(cmd, output=args.output, chunk_size=args.chunk_size)
        print(json.dumps({
            "returncode": result.returncode,
            "stdout_bytes": result.stdout_bytes,
            "stderr_bytes": result.stderr_bytes,
            "frames": result.frames,
            "output": args.output,
        }, ensure_ascii=False))
        return result.returncode
    if args.cmd == "split":
        rc = split(args.input, args.stdout, args.stderr)
        print(json.dumps({
            "input": args.input,
            "stdout": args.stdout,
            "stderr": args.stderr,
            "returncode": rc,
        }, ensure_ascii=False))
        return rc
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
