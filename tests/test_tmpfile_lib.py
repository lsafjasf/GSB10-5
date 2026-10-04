"""tmpfile_lib 自测：单实例 / 多实例并发 / 强杀残留 / 文件使用中。"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from tmpfile_lib import TempInstance, reclaim, TempFileError  # noqa: E402

APP = "testapp"


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tmpfilelib-test-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)


class TestNaming(Base):
    def test_naming_rule(self):
        inst = TempInstance(APP, self.root)
        path, fd = inst.new_temp("upload")
        os.close(fd)
        name = os.path.basename(path)
        self.assertRegex(
            name, r"^upload-\d{6}-[0-9a-f]{8}\.tmp$",
            f"bad data file name: {name}")
        self.assertRegex(
            inst.instance_id,
            r"^[A-Za-z0-9._-]+-\d+-\d{8}T\d{6}Z-[0-9a-f]{6}$")
        self.assertEqual(os.path.dirname(path), inst.dir)
        self.assertEqual(os.path.dirname(inst.dir),
                         os.path.join(self.root, APP))
        with open(os.path.join(inst.dir, "owner.json")) as fh:
            meta = json.load(fh)
        self.assertEqual(meta["pid"], os.getpid())
        self.assertEqual(meta["instance_id"], inst.instance_id)
        inst.cleanup()

    def test_unique_names(self):
        inst = TempInstance(APP, self.root)
        names = set()
        for _ in range(2000):
            path, fd = inst.new_temp("batch")
            os.close(fd)
            names.add(os.path.basename(path))
        self.assertEqual(len(names), 2000)
        inst.cleanup()

    def test_bad_purpose_rejected(self):
        inst = TempInstance(APP, self.root)
        with self.assertRaises(ValueError):
            inst.new_temp("../evil")
        inst.cleanup()


class TestSingleInstance(Base):
    def test_lifecycle(self):
        inst = TempInstance(APP, self.root)
        paths = []
        for i in range(5):
            p, fd = inst.new_temp("cache")
            os.write(fd, b"x" * 16)
            os.close(fd)
            paths.append(p)
        removed = inst.cleanup()
        self.assertEqual(len(removed), 5)
        for p in paths:
            self.assertFalse(os.path.exists(p))
        self.assertTrue(inst.closed)

    def test_double_lock_rejected(self):
        inst = TempInstance(APP, self.root, instance_id="fixed-id")
        with self.assertRaises(TempFileError):
            TempInstance(APP, self.root, instance_id="fixed-id")
        inst.cleanup()


class TestConcurrentInstances(Base):
    def test_reclaim_never_touches_live_instances(self):
        n_instances, files_each = 10, 200
        instances, errors = [], []

        def worker():
            try:
                inst = TempInstance(APP, self.root)
                for _ in range(files_each):
                    p, fd = inst.new_temp("work")
                    os.write(fd, os.urandom(64))
                    os.close(fd)
                instances.append(inst)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker)
                   for _ in range(n_instances)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(instances), n_instances)

        report = reclaim(APP, self.root, grace_seconds=0.0)
        self.assertEqual(report.scanned, n_instances)
        self.assertEqual(len(report.active_skipped), n_instances)
        self.assertEqual(report.reclaimed_files, [])
        self.assertEqual(report.residue, [])

        remaining = 0
        for inst in instances:
            remaining += sum(1 for p in inst._files if os.path.exists(p))
        self.assertEqual(remaining, n_instances * files_each)
        for inst in instances:
            inst.cleanup()


KILL9_CHILD = r"""
import os, sys, time
sys.path.insert(0, sys.argv[1])
from tmpfile_lib import TempInstance
inst = TempInstance(sys.argv[2], sys.argv[3])
for i in range(4):
    p, fd = inst.new_temp("orphan")
    os.write(fd, b"residue")
    os.close(fd)
print(inst.dir, flush=True)
time.sleep(60)
"""


class TestResidueAfterKill9(Base):
    def test_kill9_residue_reclaimed(self):
        src = os.path.join(os.path.dirname(__file__), "..", "src")
        proc = subprocess.Popen(
            [sys.executable, "-c", KILL9_CHILD, src, APP, self.root],
            stdout=subprocess.PIPE, text=True)
        inst_dir = proc.stdout.readline().strip()
        self.assertTrue(os.path.isdir(inst_dir))
        orphan_files = sorted(
            os.path.join(inst_dir, n) for n in os.listdir(inst_dir)
            if n.startswith("orphan-"))
        self.assertEqual(len(orphan_files), 4)

        proc.send_signal(signal.SIGKILL)
        proc.wait()
        self.assertEqual(proc.returncode, -signal.SIGKILL)

        for p in orphan_files:
            self.assertTrue(os.path.exists(p), "residue should survive kill -9")

        report = reclaim(APP, self.root, grace_seconds=0.0)
        self.assertEqual(len(report.residue), 1)
        self.assertEqual(report.residue[0]["dir"], inst_dir)
        self.assertEqual(sorted(report.reclaimed_files), orphan_files)
        self.assertIn(inst_dir, report.removed_dirs)
        self.assertFalse(os.path.exists(inst_dir))


HOLDER_CHILD = r"""
import os, sys, time
fd = os.open(sys.argv[1], os.O_RDONLY)
print("held", flush=True)
time.sleep(60)
"""


def _spawn_holder(path):
    proc = subprocess.Popen(
        [sys.executable, "-c", HOLDER_CHILD, path],
        stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "held"
    return proc


class TestInUseFile(Base):
    def test_open_file_not_reclaimed(self):
        # 真死实例（kill -9），但其中一个文件被另一个无关进程持有句柄
        proc = subprocess.Popen(
            [sys.executable, "-c", KILL9_CHILD,
             os.path.join(os.path.dirname(__file__), "..", "src"),
             APP, self.root],
            stdout=subprocess.PIPE, text=True)
        inst_dir = proc.stdout.readline().strip()
        proc.send_signal(signal.SIGKILL)
        proc.wait()
        files = sorted(os.path.join(inst_dir, n)
                       for n in os.listdir(inst_dir)
                       if n.startswith("orphan-"))
        self.assertEqual(len(files), 4)

        holder = _spawn_holder(files[0])
        try:
            report = reclaim(APP, self.root, grace_seconds=0.0)
        finally:
            holder.send_signal(signal.SIGKILL)
            holder.wait()

        self.assertEqual(len(report.residue), 1)
        self.assertEqual(sorted(report.reclaimed_files), sorted(files[1:]))
        self.assertEqual(len(report.in_use_skipped), 1)
        self.assertEqual(report.in_use_skipped[0]["path"], files[0])
        self.assertEqual(report.in_use_skipped[0]["reason"], "open-handle")
        self.assertTrue(os.path.exists(files[0]))
        self.assertTrue(os.path.isdir(inst_dir))

        report2 = reclaim(APP, self.root, grace_seconds=0.0)
        self.assertIn(files[0], report2.reclaimed_files)
        self.assertFalse(os.path.exists(inst_dir))

    def test_grace_period_protects_recent_files(self):
        proc = subprocess.Popen(
            [sys.executable, "-c", KILL9_CHILD,
             os.path.join(os.path.dirname(__file__), "..", "src"),
             APP, self.root],
            stdout=subprocess.PIPE, text=True)
        inst_dir = proc.stdout.readline().strip()
        proc.send_signal(signal.SIGKILL)
        proc.wait()
        files = sorted(os.path.join(inst_dir, n)
                       for n in os.listdir(inst_dir)
                       if n.startswith("orphan-"))
        old = files[0]
        fresh = files[1:]
        old_time = time.time() - 3600
        os.utime(old, (old_time, old_time))

        report = reclaim(APP, self.root, grace_seconds=600.0)
        self.assertEqual(report.reclaimed_files, [old])
        self.assertEqual(sorted(x["path"] for x in report.recent_skipped),
                         sorted(fresh))
        for p in fresh:
            self.assertTrue(os.path.exists(p))
        shutil.rmtree(inst_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
