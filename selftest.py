"""env_isolation 自测 + 基准：单用例 / 并行100 / 历史残留 / 回收失败降级 / 1000 用例基准。

运行:  python3 selftest.py [-v]
"""

import json
import os
import random
import resource
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import perf_counter
from unittest import mock

import env_isolation as ei

BENCH_N = int(os.environ.get("GSB_BENCH_N", "1000"))
BENCH_WORKERS = int(os.environ.get("GSB_BENCH_WORKERS", "100"))


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


class IsolationTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="gsb_selftest_"))
        self.run = ei.TestRun(self.base).start()

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    # 1) 单用例：准备 -> 使用 -> 回收后资源全部释放 ----
    def test_01_single_case(self):
        env = self.run.new_env("case_only").prepare()
        self.assertTrue(env.dir.is_dir())
        self.assertGreater(env.port, 1024)
        self.assertEqual(env.env["GSB_CASE_ID"], "case_only")
        (env.dir / "data.txt").write_text("hello")
        self.assertEqual((env.dir / "data.txt").read_text(), "hello")

        port = env.port
        errors = env.cleanup()
        self.assertEqual(errors, [])
        self.assertFalse(env.dir.exists())
        self.assertNotIn(port, ei._ports_in_use)

    # 2) 并行 100 个用例：目录/端口/环境变量互不可见 ----
    def test_02_parallel_100_isolation(self):
        n = 100
        results = []

        def work(idx):
            env = self.run.new_env(f"case_{idx:03d}").prepare()
            try:
                marker = env.dir / "marker"
                marker.write_text(env.case_id)
                time.sleep(random.uniform(0, 0.005))
                files = [p.name for p in env.dir.iterdir()]
                # 端口也由本用例短暂占用，验证不与他人冲突
                import socket
                with socket.socket() as s:
                    s.bind(("127.0.0.1", env.port))
                    s.listen(1)
                    time.sleep(0.001)
                return {
                    "case": env.case_id,
                    "port": env.port,
                    "dir_files": files,
                    "marker_ok": marker.read_text() == env.case_id,
                    "env_id": env.env["GSB_CASE_ID"],
                }
            finally:
                env.cleanup()

        with ThreadPoolExecutor(100) as pool:
            results = list(pool.map(work, range(n)))

        ports = [r["port"] for r in results]
        cross_visibility = [
            r for r in results
            if r["dir_files"] != ["marker"]
            or not r["marker_ok"]
            or r["env_id"] != r["case"]
        ]
        self.assertEqual(len(results), n)
        self.assertEqual(len(set(ports)), n, "端口重复：并行用例共享了端口")
        self.assertEqual(cross_visibility, [], f"存在跨用例可见: {cross_visibility[:3]}")
        print(
            f"\n[校验] 并行{n}用例: 端口唯一 {len(set(ports))}/{n},"
            f" 跨用例可见数 0, 用例目录交叉文件数 0"
        )

    # 3) 上一轮异常退出残留：属主已死的 run 目录必须被检测并清理 ----
    def test_03_residue_from_abnormal_exit(self):
        # 造一个已死亡进程的 pid
        proc = subprocess.run(["true"])
        dead_pid = proc.returncode and None  # noqa - 占位，下面用 fork 取死 pid
        # 直接 fork 一个立即退出的子进程，拿到确定已死的 pid
        dead_pid = os.fork()
        if dead_pid == 0:
            os._exit(0)
        os.waitpid(dead_pid, 0)
        self.assertFalse(ei._pid_alive(dead_pid))

        stale = self.base / f"run_{dead_pid}_1000000000_deadbe"
        (stale / "leaked_case").mkdir(parents=True)
        (stale / "leaked_case" / "state.bin").write_text("stale-data")
        (stale / "run.json").write_text(
            json.dumps({"run_id": stale.name, "pid": dead_pid, "ts": 0})
        )
        # 存活进程（自己）的 run 目录必须被保留
        alive = self.base / f"run_{os.getpid()}_1000000001_aliv00"
        alive.mkdir(parents=True)
        (alive / "run.json").write_text(
            json.dumps({"run_id": alive.name, "pid": os.getpid(), "ts": 0})
        )

        next_run = ei.TestRun(self.base).start()
        rep = next_run.sweep_report
        self.assertIn(str(stale), rep["stale_runs_detected"])
        self.assertIn(str(stale), rep["stale_runs_removed"])
        self.assertFalse(stale.exists(), "上一轮残留未被清理")
        self.assertIn(str(alive), rep["active_runs_kept"])
        self.assertTrue(alive.exists(), "活跃运行被误删")
        print(
            f"\n[残留检测] 检测到死进程残留 {len(rep['stale_runs_detected'])} 个并全部清理;"
            f" 保留活跃 run {len(rep['active_runs_kept'])} 个; 错误 {len(rep['errors'])} 个"
        )

    # 4) 回收中途失败：降级记录，后续用例不受影响，下一轮清扫残留 ----
    def test_04_cleanup_failure_degradation(self):
        victim = self.run.new_env("victim").prepare()
        victim_path = str(victim.dir)
        with mock.patch.object(
            ei.shutil, "rmtree", side_effect=OSError("simulated cleanup failure")
        ):
            errors = victim.cleanup()
        self.assertTrue(victim.dir.exists())
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].resource, "dir")
        self.assertIn("simulated cleanup failure", errors[0].error)
        log_entries = [
            json.loads(line) for line in self.run.residue_log.read_text().splitlines()
        ]
        self.assertEqual(log_entries[0]["path"], victim_path)
        print(f"\n[降级] 回收失败已记录到残留日志: {log_entries[0]['path']}")

        # 后续用例必须正常准备/回收
        follower = self.run.new_env("follower").prepare()
        self.assertTrue(follower.dir.is_dir())
        self.assertEqual(follower.cleanup(), [])
        self.assertFalse(follower.dir.exists())

        # 下一轮启动时清扫该残留
        next_run = ei.TestRun(self.base).start()
        rep = next_run.sweep_report
        self.assertIn(victim_path, rep["logged_paths_detected"])
        self.assertIn(victim_path, rep["logged_paths_removed"])
        self.assertFalse(Path(victim_path).exists())
        print(
            f"[降级] 失败后后续用例正常; 残留路径检测 {len(rep['logged_paths_detected'])} 个、"
            f"清理 {len(rep['logged_paths_removed'])} 个、错误 {len(rep['errors'])} 个"
        )

    # 5) 1000 并行用例基准：准备/回收耗时 + 内存峰值 + 隔离性 ----
    def test_05_benchmark_1000(self):
        n, workers = BENCH_N, BENCH_WORKERS
        rss0 = rss_mb()

        def prepare_one(idx):
            env = self.run.new_env(f"bench_{idx:04d}").prepare()
            (env.dir / "marker").write_text(env.case_id)
            return env

        t0 = perf_counter()
        with ThreadPoolExecutor(workers) as pool:
            envs = list(pool.map(prepare_one, range(n)))
        t_prepare = perf_counter() - t0

        ports = [e.port for e in envs]
        violations = 0
        for e in envs:
            files = list(p.name for p in e.dir.iterdir())
            if files != ["marker"] or (e.dir / "marker").read_text() != e.case_id:
                violations += 1
        self.assertEqual(len(set(ports)), n)
        self.assertEqual(violations, 0)
        rss_peak = rss_mb()

        t0 = perf_counter()
        with ThreadPoolExecutor(workers) as pool:
            err_lists = list(pool.map(lambda e: e.cleanup(), envs))
        t_cleanup = perf_counter() - t0
        errors = [err for errs in err_lists for err in errs]

        left = [p for p in self.run.run_dir.iterdir() if p.is_dir()]
        data = {
            "cases": n,
            "workers": workers,
            "prepare_seconds": round(t_prepare, 3),
            "cleanup_seconds": round(t_cleanup, 3),
            "total_seconds": round(t_prepare + t_cleanup, 3),
            "rss_peak_mb": round(rss_peak, 1),
            "rss_delta_mb": round(rss_peak - rss0, 1),
            "unique_ports": len(set(ports)),
            "cross_visibility": violations,
            "cleanup_errors": len(errors),
            "leftover_dirs": len(left),
        }
        print("\n[BENCHMARK] " + json.dumps(data, ensure_ascii=False))
        self.assertEqual(errors, [])
        self.assertEqual(left, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
