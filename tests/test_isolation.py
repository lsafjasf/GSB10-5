"""testenv 自测：单用例 / 并行 100 / 异常退出残留 / 回收中途失败。"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from testenv import IsolatedEnv, destroy_all, isolated_env, live_namespaces, sweep_residue


class Base(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="testenv_ut_")

    def tearDown(self):
        sweep_residue(self.base, min_age_s=0)
        shutil.rmtree(self.base, ignore_errors=True)


class TestSingleCase(Base):
    def test_single_env_lifecycle(self):
        env = IsolatedEnv(base_dir=self.base, env_vars={"TOKEN": "abc"})
        self.assertTrue(os.path.isdir(env.workdir))
        self.assertTrue(os.path.isdir(env.state_dir))
        env_key = f"TESTENV_{env.namespace}_TOKEN"
        self.assertEqual(os.environ.get(env_key), "abc")
        self.assertIn(env.namespace, live_namespaces())

        port = env.allocate_port(hold=True)
        self.assertGreater(port, 0)

        # 用例内写状态
        state_file = os.path.join(env.state_dir, "result.json")
        with open(state_file, "w") as fh:
            json.dump({"ok": True}, fh)

        env.destroy()
        self.assertFalse(os.path.exists(env.path))
        self.assertIsNone(os.environ.get(env_key))
        self.assertNotIn(env.namespace, live_namespaces())
        self.assertFalse(env.degraded)
        self.assertEqual(env.reclaim_errors, [])

    def test_context_manager_restores_cwd_and_env(self):
        old_cwd = os.getcwd()
        with isolated_env(base_dir=self.base) as env:
            self.assertEqual(os.getcwd(), env.workdir)
        self.assertEqual(os.getcwd(), old_cwd)
        self.assertFalse(os.path.exists(env.path))


class TestParallel100(Base):
    def test_100_parallel_envs_no_cross_visibility(self):
        results = {}

        def case(i):
            env = IsolatedEnv(base_dir=self.base)
            port = env.allocate_port(hold=True)
            # 写私有状态文件，内容带自己的序号
            with open(os.path.join(env.state_dir, "state"), "w") as fh:
                fh.write(f"case-{i}")
            # 校验：自己目录里只能看到自己的状态
            with open(os.path.join(env.state_dir, "state")) as fh:
                content = fh.read()
            # 校验：看不到任何其它用例的目录内容
            others_visible = []
            run_root = os.path.dirname(env.path)
            for name in os.listdir(run_root):
                other_state = os.path.join(run_root, name, "state", "state")
                if name != env.namespace and os.path.exists(other_state):
                    with open(other_state) as fh:
                        if fh.read() == f"case-{i}":
                            others_visible.append(name)
            return {
                "i": i,
                "namespace": env.namespace,
                "path": env.path,
                "port": port,
                "content": content,
                "others_visible": others_visible,
                "env": env,
            }

        with ThreadPoolExecutor(max_workers=32) as pool:
            for res in pool.map(case, range(100)):
                results[res["i"]] = res

        namespaces = [r["namespace"] for r in results.values()]
        ports = [r["port"] for r in results.values()]
        paths = [r["path"] for r in results.values()]

        self.assertEqual(len(set(namespaces)), 100, "命名空间必须全局唯一")
        self.assertEqual(len(set(ports)), 100, "并行分配的端口不得重复")
        self.assertEqual(len(set(paths)), 100, "目录必须互不相同")
        for res in results.values():
            self.assertEqual(res["content"], f"case-{res['i']}")
            self.assertEqual(res["others_visible"], [])

        stats = destroy_all()
        self.assertEqual(stats["destroyed"], 100)
        self.assertEqual(stats["degraded"], 0)
        for res in results.values():
            self.assertFalse(os.path.exists(res["path"]))
        self.assertEqual(live_namespaces(), set())


class TestResidueFromCrash(Base):
    def test_orphaned_env_detected_and_swept(self):
        # 子进程创建环境后 os._exit 模拟异常退出（不跑 atexit/destroy）
        child_code = (
            "import os, sys, json;"
            "sys.path.insert(0, %r);"
            "from testenv import IsolatedEnv;"
            "e = IsolatedEnv(base_dir=%r);"
            "print(json.dumps({'namespace': e.namespace, 'path': e.path}), flush=True);"
            "os._exit(9)"
            % (os.path.dirname(os.path.dirname(os.path.abspath(__file__))), self.base)
        )
        proc = subprocess.run(
            [sys.executable, "-c", child_code],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(proc.returncode, 9)
        orphan = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertTrue(os.path.isdir(orphan["path"]), "残留目录应存在")

        report = sweep_residue(self.base, min_age_s=0)
        self.assertIn(orphan["path"], report.removed)
        self.assertFalse(os.path.exists(orphan["path"]))
        self.assertEqual(report.failed, [])

    def test_live_env_not_swept(self):
        env = IsolatedEnv(base_dir=self.base)
        report = sweep_residue(self.base, min_age_s=0)
        self.assertTrue(os.path.isdir(env.path), "存活进程的环境不得被清理")
        self.assertIn(env.path, report.live_skipped)
        env.destroy()


class TestReclaimFailure(Base):
    def test_reclaim_failure_degrades_without_raising(self):
        survivor = IsolatedEnv(base_dir=self.base)
        victim = IsolatedEnv(base_dir=self.base)

        real_rmtree = shutil.rmtree

        def flaky_rmtree(path, *a, **kw):
            if os.path.abspath(path) == os.path.abspath(victim.path):
                raise OSError("注入的回收失败")
            return real_rmtree(path, *a, **kw)

        with mock.patch("testenv.isolation.shutil.rmtree", side_effect=flaky_rmtree):
            victim.destroy()  # 不得抛出
            survivor.destroy()  # 后续用例不受影响

        self.assertTrue(victim.degraded)
        self.assertTrue(victim.reclaim_errors)
        self.assertTrue(os.path.isdir(victim.path), "失败时目录应保留待重试")
        self.assertFalse(os.path.exists(survivor.path), "后续用例回收必须正常")

        # 下一轮 sweep 兜底清理降级残留
        report = sweep_residue(self.base, min_age_s=0)
        self.assertIn(victim.path, report.removed)
        self.assertFalse(os.path.exists(victim.path))


if __name__ == "__main__":
    unittest.main(verbosity=2)
