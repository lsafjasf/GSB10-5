"""子进程 stdout/stderr 输出流合并库（仅 Python 3 标准库）。

合并策略：按行为主，超长行按固定大小切块（详见 MERGE_STRATEGY.md）。

合并流是二进制的、自描述的、可逆的长度前缀帧序列：

    帧 = 标签(1 字节) + b' ' + 长度(ASCII 十进制) + b'\\n' + 负载(长度字节)

标签为 b'1' 表示 stdout，b'2' 表示 stderr。负载就是原始字节，
不做任何转义；把同一标签各帧的负载按顺序拼起来，即得到该流的
原始内容（字节级一致）。

内存上界：每个流的内部缓冲区 < max_record + read_chunk，
与输出总量无关，因此处理超大输出不会无界增长。
"""

from __future__ import annotations

import os
import selectors
import subprocess

STDOUT_TAG = b"1"
STDERR_TAG = b"2"

DEFAULT_MAX_RECORD = 64 * 1024
_READ_CHUNK = 64 * 1024


class MergeFormatError(ValueError):
    """合并流格式非法（损坏或被截断）。"""


def _emit(sink, tag: bytes, payload: bytes) -> None:
    sink.write(tag + b" " + str(len(payload)).encode("ascii") + b"\n" + payload)


def _drain(state_buf: bytearray, tag: bytes, sink, max_record: int,
           flush_all: bool = False) -> None:
    """从单个流的缓冲区取出可以发出的帧。

    - 前 max_record 字节内有换行：发出整行（含换行）；
    - 没有换行但缓冲已达 max_record：超长行切块发出；
    - flush_all：EOF 时发出尾部不完整行（无换行结尾）。
    """
    buf = state_buf
    while buf:
        newline = buf.find(b"\n", 0, max_record)
        if newline != -1:
            end = newline + 1
        elif len(buf) >= max_record:
            end = max_record
        elif flush_all:
            end = len(buf)
        else:
            return
        _emit(sink, tag, bytes(buf[:end]))
        del buf[:end]


def merge(stdout, stderr, sink, max_record: int = DEFAULT_MAX_RECORD) -> None:
    """合并两个二进制可读流（文件对象，需有 fileno()）到 sink。

    stdout/stderr 可为 None，表示该流不存在（例如只捕获一个流）。
    sink 必须是可写字节的文件对象。
    """
    if max_record <= 0:
        raise ValueError("max_record 必须为正整数")

    selector = selectors.DefaultSelector()
    states = []
    for tag, stream in ((STDOUT_TAG, stdout), (STDERR_TAG, stderr)):
        if stream is None:
            continue
        buf = bytearray()
        states.append(buf)
        selector.register(stream, selectors.EVENT_READ, (tag, buf))

    try:
        while selector.get_map():
            for key, _events in selector.select():
                tag, buf = key.data
                chunk = os.read(key.fileobj.fileno(), _READ_CHUNK)
                if chunk:
                    buf.extend(chunk)
                    _drain(buf, tag, sink, max_record)
                else:
                    selector.unregister(key.fileobj)
                    _drain(buf, tag, sink, max_record, flush_all=True)
    finally:
        selector.close()


def run_merged(argv, sink, max_record: int = DEFAULT_MAX_RECORD,
               **popen_kwargs) -> int:
    """启动子进程，把其 stdout/stderr 合并写入 sink，返回退出码。"""
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **popen_kwargs,
    )
    try:
        merge(proc.stdout, proc.stderr, sink, max_record=max_record)
    finally:
        proc.stdout.close()
        proc.stderr.close()
    return proc.wait()


def _pump(fd: int, data: bytes) -> None:
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]
    finally:
        os.close(fd)


def merge_bytes(out: bytes, err: bytes, max_record: int = DEFAULT_MAX_RECORD) -> bytes:
    """内存便捷接口：合并两段字节，返回合并后的帧流字节。

    内部走真实管道（写端用后台线程，避免管道缓冲写满死锁），
    行为与 merge() 完全一致，主要用于测试与小数据场景。
    """
    import io
    import threading

    streams = []
    threads = []
    for data in (out, err):
        read_fd, write_fd = os.pipe()
        streams.append(os.fdopen(read_fd, "rb"))
        thread = threading.Thread(target=_pump, args=(write_fd, data))
        thread.start()
        threads.append(thread)
    sink = io.BytesIO()
    try:
        merge(streams[0], streams[1], sink, max_record=max_record)
    finally:
        for stream in streams:
            stream.close()
        for thread in threads:
            thread.join()
    return sink.getvalue()


def _read_exact(stream, size: int) -> bytes:
    parts = []
    remaining = size
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            raise MergeFormatError("合并流被截断：负载不足")
        parts.append(chunk)
        remaining -= len(chunk)
    return b"".join(parts)


def split(merged, out_stdout, out_stderr,
          max_record: int | None = None) -> None:
    """把 merge 产生的合并流拆回两个原始流。

    out_stdout/out_stderr 可为 None，表示丢弃对应流。
    max_record 给定时校验单帧负载不超过该值（防御性检查）。
    """
    while True:
        header = merged.readline()
        if not header:
            return
        if len(header) < 4 or header[1:2] != b" ":
            raise MergeFormatError("非法帧头: %r" % header)
        tag = header[:1]
        try:
            length = int(header[2:].rstrip(b"\n"))
        except ValueError as exc:
            raise MergeFormatError("非法帧长度: %r" % header) from exc
        if length < 0:
            raise MergeFormatError("负的帧长度")
        if max_record is not None and length > max_record:
            raise MergeFormatError("帧超过 max_record: %d" % length)
        payload = _read_exact(merged, length)
        if tag == STDOUT_TAG:
            target = out_stdout
        elif tag == STDERR_TAG:
            target = out_stderr
        else:
            raise MergeFormatError("非法流标签: %r" % tag)
        if target is not None:
            target.write(payload)
