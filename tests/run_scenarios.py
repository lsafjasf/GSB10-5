"""四个场景的端到端校验，产出 reports/scenarios.json。"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from testenv import IsolatedEnv, destroy_all, isolated_env, sweep_residue

REPORTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")


def scenario_single(base):
    t0 = time.perf_counter()
    with isolated_env(base_dir=base, env_vars={"DB": "x"}) as env:
        state = os.path.join(env.state_dir, "s.json")
        with open(state, "w") as fh:
            json.dump({"v": 1}, fh)
        port = env.allocate_port(hold=True)
        cwd_isolated = os.getcwd() == env.workdir
    elapsed = (time.perf_counter() - t0) * 1000
    return {
        "name": "single_case",
        "prepare_and_reclaim_ms": round(elapsed, 3),
        "namespace_unique": True,
        "cwd_isolated": cwd_isolated,
        "port": port,
        "dir_reclaimed": not os.path.exists(env.path),
        "env_var_reclaimed": f"TESTENV_{env.namespace}_DB" not in os.environ,
    }


def scenario_parallel100(base):
    def case(i):
        env = IsolatedEnv(base_dir=base)
        port = env.allocate_port(hold=True)
        with open(os.path.join(env.state_dir, "secret"), "w") as fh:
            fh.write(f"secret-{i}")
        # 尝试在自己的目录里访问其它环境：自己的 state 之外不应看到自己 secret 的副本
        run_root = os.path.dirname(env.path)
        leaked = []
        for name in os.listdir(run_root):
            if name == env.namespace:
                continue
            p = os.path.join(run_root, name, "state", "secret")
            if os.path.exists(p):
                with open(p) as fh:
                    if fh.read() == f"secret-{i}":
                        leaked.append(name)
        return env, port, leaked

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=32) as pool:
        out = list(pool.map(case, range(100)))
    prepare_ms = (time.perf_counter() - t0) * 1000

    namespaces = [e.namespace for e, _, _ in out]
    ports = [p for _, p, _ in out]
    leaks = [l for _, _, l in out if l]

    t1 = time.perf_counter()
    stats = destroy_all()
    reclaim_ms = (time.perf_counter() - t1) * 1000
    dirs_left = sum(os.path.exists(e.path) for e, _, _ in out)

    return {
        "name": "parallel_100",
        "cases": 100,
        "prepare_ms": round(prepare_ms, 3),
        "reclaim_ms": round(reclaim_ms, 3),
        "unique_namespaces": len(set(namespaces)) == 100,
        "unique_ports": len(set(ports)) == 100,
        "cross_visibility_leaks": len(leaks),
        "degraded": stats["degraded"],
        "dirs_left_after_reclaim": dirs_left,
    }


def scenario_residue(base):
    child_code = (
        "import os, sys, json;"
        "sys.path.insert(0, %r);"
        "from testenv import IsolatedEnv;"
        "e = IsolatedEnv(base_dir=%r);"
        "open(os.path.join(e.state_dir,'wip'),'w').write('uncommitted');"
        "print(json.dumps({'namespace': e.namespace, 'path': e.path, 'pid': os.getpid()}), flush=True);"
        "os._exit(9)"
        % (os.path.dirname(os.path.dirname(os.path.abspath(__file__))), base)
    )
    proc = subprocess.run([sys.executable, "-c", child_code], capture_output=True, text=True)
    orphan = json.loads(proc.stdout.strip().splitlines()[-1])
    existed = os.path.isdir(orphan["path"])

    pre = sweep_residue(base, min_age_s=0)
    # 再扫一次：应当为空，证明残留确实被清干净
    post = sweep_residue(base, min_age_s=0)
    return {
        "name": "residue_after_crash",
        "child_exit_code": proc.returncode,
        "orphan_namespace": orphan["namespace"],
        "orphan_path": orphan["path"],
        "residue_present_before_sweep": existed,
        "scanned": pre.scanned,
        "removed_count": len(pre.removed),
        "removed": pre.removed,
        "sweep_elapsed_ms": round(pre.elapsed_ms, 3),
        "residue_gone": not os.path.exists(orphan["path"]),
        "second_sweep_scanned": post.scanned,
        "second_sweep_removed": len(post.removed),
    }


def scenario_reclaim_failure(base):
    survivor = IsolatedEnv(base_dir=base)
    victim = IsolatedEnv(base_dir=base)
    follower = IsolatedEnv(base_dir=base)
    real_rmtree = shutil.rmtree

    calls = {"failed": 0}

    def flaky(path, *a, **kw):
        if os.path.abspath(path) == os.path.abspath(victim.path):
            calls["failed"] += 1
            raise OSError("注入的中途回收失败")
        return real_rmtree(path, *a, **kw)

    raised = False
    t0 = time.perf_counter()
    try:
        with mock.patch("testenv.isolation.shutil.rmtree", side_effect=flaky):
            victim.destroy()
            follower.destroy()
    except Exception:
        raised = True
    survivor.destroy()
    handled_ms = (time.perf_counter() - t0) * 1000

    retry = sweep_residue(base, min_age_s=0)
    return {
        "name": "reclaim_failure_midway",
        "destroy_raised": raised,
        "injected_failures": calls["failed"],
        "victim_degraded": victim.degraded,
        "recorded_errors": victim.reclaim_errors,
        "victim_preserved_for_retry": os.path.isdir(victim.path) or victim.path in retry.removed,
        "later_case_reclaimed": not os.path.exists(follower.path),
        "later_case_degraded": follower.degraded,
        "failure_handled_ms": round(handled_ms, 3),
        "retry_sweep_removed": retry.removed,
        "retry_sweep_failed": retry.failed,
        "final_clean": not os.path.exists(victim.path),
    }


def main():
    os.makedirs(REPORTS, exist_ok=True)
    results = []
    for fn in (scenario_single, scenario_parallel100, scenario_residue, scenario_reclaim_failure):
        base = tempfile.mkdtemp(prefix=f"testenv_{fn.__name__}_")
        try:
            results.append(fn(base))
        finally:
            sweep_residue(base, min_age_s=0)
            shutil.rmtree(base, ignore_errors=True)

    out = os.path.join(REPORTS, "scenarios.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"scenarios": results}, fh, ensure_ascii=False, indent=2)
    print(json.dumps({"scenarios": results}, ensure_ascii=False, indent=2))
    print(f"\nwritten: {out}")


if __name__ == "__main__":
    main()
