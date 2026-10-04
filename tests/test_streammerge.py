"""streammerge 自测：python3 -m unittest discover -s tests -v"""

import io
import os
import random
import subprocess
import sys
import tempfile
import threading
import tracemalloc
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import streammerge as sm  # noqa: E402

PY = sys.executable


def run_child_script(script: str, chunk_size: int = sm.DEFAULT_CHUNK_SIZE):
    """用真实子进程跑一段 python 代码并合并其输出。"""
    with tempfile.TemporaryDirectory() as tmp:
        merged = os.path.join(tmp, "merged.mox1")
        result = sm.merge([PY, "-c", script], output=merged,
                          chunk_size=chunk_size)
        out_path = os.path.join(tmp, "o.bin")
        err_path = os.path.join(tmp, "e.bin")
        rc = sm.split(merged, out_path, err_path)
        with open(out_path, "rb") as fh:
            out = fh.read()
        with open(err_path, "rb") as fh:
            err = fh.read()
    return result, rc, out, err


def merge_inprocess(out_chunks, err_chunks, chunk_size=sm.DEFAULT_CHUNK_SIZE,
                    sink=None):
    """用 in-process 管道 + 写线程驱动 merge_pipes，返回 (writer, 统计)。"""
    o_r, o_w = os.pipe()
    e_r, e_w = os.pipe()

    def writer_thread(fd, chunks):
        with os.fdopen(fd, "wb") as fh:
            for c in chunks:
                fh.write(c)
                fh.flush()

    t1 = threading.Thread(target=writer_thread, args=(o_w, out_chunks))
    t2 = threading.Thread(target=writer_thread, args=(e_w, err_chunks))
    t1.start()
    t2.start()
    if sink is None:
        sink = io.BytesIO()
    with sm.FrameWriter(sink, chunk_size=chunk_size) as fw:
        with os.fdopen(o_r, "rb") as o_rd, os.fdopen(e_r, "rb") as e_rd:
            stats = sm.merge_pipes(o_rd, e_rd, fw)
    t1.join()
    t2.join()
    return fw, stats, sink


class TestRealSubprocess(unittest.TestCase):
    def test_stdout_only(self):
        script = ("import sys; [sys.stdout.write('out-%d\\n' % i) "
                  "for i in range(1000)]; sys.stdout.flush()")
        result, rc, out, err = run_child_script(script)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(rc, 0)
        self.assertEqual(out, b"".join(b"out-%d\n" % i for i in range(1000)))
        self.assertEqual(err, b"")

    def test_stderr_only(self):
        script = ("import sys; [sys.stderr.write('err-%d\\n' % i) "
                  "for i in range(1000)]; sys.stderr.flush()")
        result, rc, out, err = run_child_script(script)
        self.assertEqual(rc, 0)
        self.assertEqual(out, b"")
        self.assertEqual(err, b"".join(b"err-%d\n" % i for i in range(1000)))

    def test_alternating(self):
        script = (
            "import sys\n"
            "for i in range(2000):\n"
            "    (sys.stdout if i % 2 == 0 else sys.stderr).write('L%d\\n' % i)\n"
            "    sys.stdout.flush(); sys.stderr.flush()\n"
        )
        result, rc, out, err = run_child_script(script)
        self.assertEqual(rc, 0)
        self.assertEqual(out, b"".join(b"L%d\n" % i for i in range(0, 2000, 2)))
        self.assertEqual(err, b"".join(b"L%d\n" % i for i in range(1, 2000, 2)))

    def test_no_trailing_newline(self):
        script = ("import sys; sys.stdout.write('tail-without-newline');"
                  " sys.stderr.write('E-no-nl');"
                  " sys.stdout.flush(); sys.stderr.flush()")
        result, rc, out, err = run_child_script(script)
        self.assertEqual(out, b"tail-without-newline")
        self.assertEqual(err, b"E-no-nl")

    def test_very_long_line(self):
        script = ("import sys; sys.stdout.write('x' * 5000000 + '\\n');"
                  " sys.stdout.flush()")
        result, rc, out, err = run_child_script(script, chunk_size=4096)
        self.assertEqual(out, b"x" * 5_000_000 + b"\n")
        self.assertEqual(err, b"")
        self.assertEqual(result.stdout_bytes, 5_000_001)

    def test_long_line_frame_bound(self):
        """超长行被确定性切成 <= chunk_size 的连续帧。"""
        payload = b"y" * 1_000_000
        fw, stats, sink = merge_inprocess([payload], [], chunk_size=1024)
        sink.seek(0)
        max_payload = 0
        frames = 0
        for tag, data in sm.frames_iter(sink):
            self.assertEqual(tag, sm.TAG_STDOUT)
            max_payload = max(max_payload, len(data))
            frames += 1
        self.assertLessEqual(max_payload, 1024)
        self.assertGreater(frames, 900)  # ~977 帧
        self.assertEqual(stats, (1_000_000, 0))

    def test_exit_code(self):
        result, rc, out, err = run_child_script("import sys; sys.exit(7)")
        self.assertEqual(result.returncode, 7)
        self.assertEqual(rc, 7)

    @unittest.skipUnless(hasattr(os, "kill"), "POSIX only")
    def test_killed_by_signal(self):
        import signal
        script = "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"
        result, rc, out, err = run_child_script(script)
        self.assertEqual(result.returncode, -signal.SIGKILL)
        self.assertEqual(rc, -signal.SIGKILL)

    def test_empty_streams(self):
        result, rc, out, err = run_child_script("pass")
        self.assertEqual((out, err), (b"", b""))
        self.assertEqual(rc, 0)


class TestInProcessPipes(unittest.TestCase):
    def test_fuzz_interleave(self):
        rng = random.Random(20261005)
        out_parts, err_parts = [], []
        for _ in range(300):
            n = rng.randrange(0, 3000)
            chunk = bytes(rng.randrange(256) for _ in range(n))
            (out_parts if rng.random() < 0.5 else err_parts).append(chunk)
        fw, stats, sink = merge_inprocess(out_parts, err_parts,
                                          chunk_size=512)
        self.assertEqual(stats, (sum(map(len, out_parts)),
                                 sum(map(len, err_parts))))
        sink.seek(0)
        got_out, got_err = io.BytesIO(), io.BytesIO()
        sm.split(sink, got_out, got_err)
        self.assertEqual(got_out.getvalue(), b"".join(out_parts))
        self.assertEqual(got_err.getvalue(), b"".join(err_parts))

    def test_binary_and_zero_chunks(self):
        out = [b"\x00\xff\n\r" * 100, b""]
        err = [bytes(range(256)) * 4]
        fw, stats, sink = merge_inprocess(out, err, chunk_size=333)
        sink.seek(0)
        got_out, got_err = io.BytesIO(), io.BytesIO()
        sm.split(sink, got_out, got_err)
        self.assertEqual(got_out.getvalue(), b"".join(out))
        self.assertEqual(got_err.getvalue(), b"".join(err))


class TestFormat(unittest.TestCase):
    def test_bad_magic(self):
        with self.assertRaises(sm.FormatError):
            list(sm.frames_iter(io.BytesIO(b"NOPE....")))

    def test_truncated_payload(self):
        data = sm.MAGIC + bytes([1]) + (10).to_bytes(4, "big") + b"abc"
        with self.assertRaises(sm.FormatError):
            list(sm.frames_iter(io.BytesIO(data)))

    def test_unknown_tag(self):
        data = sm.MAGIC + bytes([9]) + (0).to_bytes(4, "big")
        with self.assertRaises(sm.FormatError):
            list(sm.frames_iter(io.BytesIO(data)))

    def test_split_roundtrip_bytes(self):
        sink = io.BytesIO()
        with sm.FrameWriter(sink, chunk_size=100) as fw:
            fw.write_data(sm.TAG_STDOUT, b"a" * 250)
            fw.write_data(sm.TAG_STDERR, b"b" * 10)
            fw.write_exit(3)
        sink.seek(0)
        out, err = io.BytesIO(), io.BytesIO()
        rc = sm.split(sink, out, err)
        self.assertEqual(rc, 3)
        self.assertEqual(out.getvalue(), b"a" * 250)
        self.assertEqual(err.getvalue(), b"b" * 10)


class TestMemoryBound(unittest.TestCase):
    """内存上界：两个量级下 tracemalloc 峰值近似不变，证明有界。"""

    def _peak_for_payload(self, nbytes: int) -> int:
        chunk = os.urandom(65536)
        chunks = [chunk] * (nbytes // len(chunk))
        tracemalloc.start()
        tracemalloc.reset_peak()
        with open(os.devnull, "wb") as devnull:
            fw, stats, sink = merge_inprocess(chunks, chunks,
                                              chunk_size=65536, sink=devnull)
        _cur, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return peak

    def test_peak_is_scale_invariant(self):
        peak_small = self._peak_for_payload(2 * 1024 * 1024)     # 2MB/流
        peak_large = self._peak_for_payload(64 * 1024 * 1024)    # 64MB/流
        # 输出量放大 32 倍，峰值增长必须 < 1MB（理论上应几乎为 0）
        self.assertLess(peak_large - peak_small, 1024 * 1024)
        # 绝对上界：64MB/流 时 Python 分配峰值 < 4MB
        self.assertLess(peak_large, 4 * 1024 * 1024)


class TestCli(unittest.TestCase):
    def test_merge_split_cli(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cli = os.path.join(root, "streammerge.py")
        with tempfile.TemporaryDirectory() as tmp:
            merged = os.path.join(tmp, "m.mox1")
            cmd = [PY, cli, "merge", "-o", merged, "--",
                   PY, "-c",
                   "import sys; sys.stdout.write('O1\\n');"
                   " sys.stderr.write('E1\\n');"
                   " sys.stdout.flush(); sys.stderr.flush()"]
            proc = subprocess.run(cmd, capture_output=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            out_p, err_p = (os.path.join(tmp, "o"), os.path.join(tmp, "e"))
            proc = subprocess.run(
                [PY, cli, "split", "-i", merged,
                 "--stdout", out_p, "--stderr", err_p],
                capture_output=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(out_p, "rb") as fh:
                self.assertEqual(fh.read(), b"O1\n")
            with open(err_p, "rb") as fh:
                self.assertEqual(fh.read(), b"E1\n")


if __name__ == "__main__":
    unittest.main()
