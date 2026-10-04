"""stream_merge 自测：python3 -m unittest test_stream_merge -v"""

import hashlib
import io
import os
import pathlib
import random
import subprocess
import sys
import tempfile
import tracemalloc
import unittest

import stream_merge
from stream_merge import MergeFormatError

HERE = pathlib.Path(__file__).parent
DATA = HERE / "testdata"
MAX_RECORD = stream_merge.DEFAULT_MAX_RECORD


def run_child(code: str, max_record: int = MAX_RECORD):
    """跑一个 Python 子进程，返回 (退出码, 合并字节)。"""
    sink = io.BytesIO()
    rc = stream_merge.run_merged(
        [sys.executable, "-u", "-c", code], sink, max_record=max_record)
    return rc, sink.getvalue()


def split_bytes(merged: bytes):
    out, err = io.BytesIO(), io.BytesIO()
    stream_merge.split(io.BytesIO(merged), out, err)
    return out.getvalue(), err.getvalue()


def frame_sizes(merged: bytes):
    """解析合并流，返回 (标签, 负载长度) 列表。"""
    frames = []
    stream = io.BytesIO(merged)
    while True:
        header = stream.readline()
        if not header:
            return frames
        tag = header[:1]
        length = int(header[2:].rstrip(b"\n"))
        stream.read(length)
        frames.append((tag, length))


class GoldenFilesTest(unittest.TestCase):
    """拆分对拍：testdata 中每个 case 必须双向一致。"""

    def test_all_cases_roundtrip(self):
        cases = sorted(p.name[:-7] for p in DATA.glob("*.merged"))
        self.assertGreaterEqual(len(cases), 6)
        for name in cases:
            with self.subTest(case=name):
                out = (DATA / f"{name}.stdout").read_bytes()
                err = (DATA / f"{name}.stderr").read_bytes()
                merged = (DATA / f"{name}.merged").read_bytes()
                got_out, got_err = split_bytes(merged)
                self.assertEqual(got_out, out, "split 还原 stdout 不一致")
                self.assertEqual(got_err, err, "split 还原 stderr 不一致")
                self.assertEqual(stream_merge.merge_bytes(out, err), merged,
                                 "merge 重新生成与对拍文件不一致")


class MergeBehaviorTest(unittest.TestCase):
    def test_only_stdout(self):
        rc, merged = run_child(
            "import sys; [print(f'out-{i}') for i in range(100)]")
        self.assertEqual(rc, 0)
        out, err = split_bytes(merged)
        self.assertEqual(out, b"".join(b"out-%d\n" % i for i in range(100)))
        self.assertEqual(err, b"")
        self.assertTrue(all(t == b"1" for t, _ in frame_sizes(merged)))

    def test_only_stderr(self):
        rc, merged = run_child(
            "import sys; [sys.stderr.write(f'e-{i}\\n') for i in range(50)]")
        self.assertEqual(rc, 0)
        out, err = split_bytes(merged)
        self.assertEqual(out, b"")
        self.assertEqual(err, b"".join(b"e-%d\n" % i for i in range(50)))

    def test_alternating_streams(self):
        code = (
            "import sys\n"
            "for i in range(200):\n"
            "    sys.stdout.write(f'O{i}\\n'); sys.stdout.flush()\n"
            "    sys.stderr.write(f'E{i}\\n'); sys.stderr.flush()\n"
        )
        rc, merged = run_child(code)
        self.assertEqual(rc, 0)
        out, err = split_bytes(merged)
        self.assertEqual(out, b"".join(b"O%d\n" % i for i in range(200)))
        self.assertEqual(err, b"".join(b"E%d\n" % i for i in range(200)))
        tags = [t for t, _ in frame_sizes(merged)]
        self.assertIn(b"1", tags)
        self.assertIn(b"2", tags)

    def test_no_trailing_newline(self):
        code = (
            "import sys\n"
            "sys.stdout.write('tail-no-newline'); sys.stdout.flush()\n"
            "sys.stderr.write('err-line\\nerr-tail'); sys.stderr.flush()\n"
        )
        rc, merged = run_child(code)
        self.assertEqual(rc, 0)
        out, err = split_bytes(merged)
        self.assertEqual(out, b"tail-no-newline")
        self.assertEqual(err, b"err-line\nerr-tail")

    def test_long_line_split_into_bounded_frames(self):
        line_len = 3 * 1024 * 1024 + 123  # 3MB+，远超 64KB max_record
        code = f"import sys; sys.stdout.write('L' * {line_len} + '\\n')"
        rc, merged = run_child(code)
        self.assertEqual(rc, 0)
        frames = frame_sizes(merged)
        self.assertGreater(len(frames), 1, "超长行应被切成多帧")
        self.assertTrue(all(n <= MAX_RECORD for _, n in frames),
                        "每帧负载不得超过 max_record")
        self.assertEqual(sum(n for _, n in frames), line_len + 1)
        out, err = split_bytes(merged)
        self.assertEqual(out, b"L" * line_len + b"\n")
        self.assertEqual(err, b"")

    def test_empty_streams(self):
        rc, merged = run_child("pass")
        self.assertEqual(rc, 0)
        self.assertEqual(merged, b"")
        self.assertEqual(split_bytes(merged), (b"", b""))

    def test_exit_code_passthrough(self):
        rc, merged = run_child(
            "import sys; print('bye'); sys.exit(3)")
        self.assertEqual(rc, 3)
        out, _ = split_bytes(merged)
        self.assertEqual(out, b"bye\n")

    def test_binary_and_arbitrary_bytes(self):
        rng = random.Random(20261005)
        out = bytes(rng.randrange(256) for _ in range(300_000))
        err = bytes(rng.randrange(256) for _ in range(200_000))
        merged = stream_merge.merge_bytes(out, err)
        self.assertEqual(split_bytes(merged), (out, err))

    def test_fuzz_roundtrip(self):
        rng = random.Random(7)
        for trial in range(30):
            with self.subTest(trial=trial):
                def make():
                    parts = []
                    for _ in range(rng.randrange(0, 40)):
                        n = rng.randrange(0, 300)
                        chunk = bytes(rng.randrange(256) for _ in range(n))
                        parts.append(chunk)
                        if rng.random() < 0.7:
                            parts.append(b"\n")
                    return b"".join(parts)
                out, err = make(), make()
                merged = stream_merge.merge_bytes(out, err)
                self.assertEqual(split_bytes(merged), (out, err))

    def test_corrupt_stream_rejected(self):
        with self.assertRaises(MergeFormatError):
            split_bytes(b"garbage not a frame\n")
        with self.assertRaises(MergeFormatError):
            split_bytes(b"1 100\nshort")  # 声明 100 字节但只有 5
        with self.assertRaises(MergeFormatError):
            split_bytes(b"9 3\nabc")      # 非法标签


class MemoryBoundTest(unittest.TestCase):
    """大量输出：合并方内存必须有上界，且内容不丢。"""

    LINES_PER_STREAM = 250_000  # 两流合计约 65MB

    @staticmethod
    def _line(tag: bytes, i: int) -> bytes:
        return tag + b"-%08d-" % i + b"x" * 100 + b"\n"

    def _expected_digest(self, tag: bytes) -> str:
        digest = hashlib.sha256()
        for i in range(self.LINES_PER_STREAM):
            digest.update(self._line(tag, i))
        return digest.hexdigest()

    def test_bounded_memory_no_loss(self):
        child = (
            "import sys\n"
            f"n = {self.LINES_PER_STREAM}\n"
            "for i in range(n):\n"
            "    sys.stdout.write('o-%08d-' % i + 'x' * 100 + '\\n')\n"
            "    sys.stderr.write('e-%08d-' % i + 'x' * 100 + '\\n')\n"
            "    if i % 1000 == 0: sys.stdout.flush(); sys.stderr.flush()\n"
        )
        with tempfile.TemporaryFile() as merged_file, \
             tempfile.TemporaryFile() as out_file, \
             tempfile.TemporaryFile() as err_file:
            tracemalloc.start()
            try:
                rc = stream_merge.run_merged(
                    [sys.executable, "-u", "-c", child], merged_file)
                merged_file.seek(0)
                stream_merge.split(merged_file, out_file, err_file)
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()

            self.assertEqual(rc, 0)
            peak_mb = peak / 1024 / 1024
            self.assertLess(
                peak_mb, 16,
                f"合并+拆分峰值内存 {peak_mb:.1f}MB 超过 16MB 上界")

            for fobj, tag in ((out_file, b"o"), (err_file, b"e")):
                fobj.seek(0)
                digest = hashlib.sha256()
                while True:
                    block = fobj.read(1 << 20)
                    if not block:
                        break
                    digest.update(block)
                self.assertEqual(digest.hexdigest(),
                                 self._expected_digest(tag),
                                 f"流 {tag} 内容丢失或损坏")


if __name__ == "__main__":
    unittest.main()
