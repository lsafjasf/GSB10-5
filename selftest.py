"""Self-tests and benchmarks for cache_key.py.  Run: python3 selftest.py"""

import os
import resource
import shutil
import sys
import tempfile
import time
import tracemalloc

from cache_key import (
    PlatformInfo,
    Toolchain,
    build_key_payload,
    compute_key,
    digest_payload,
    lookup,
    source_manifest,
)

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"[{PASS if ok else FAIL}] {name}" + (f"  | {detail}" if detail else ""))


def make_source_tree(root):
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    with open(os.path.join(root, "src", "main.c"), "w") as f:
        f.write('#include "util.h"\nint main(void){return util();}\n')
    with open(os.path.join(root, "src", "util.h"), "w") as f:
        f.write("int util(void);\n")
    with open(os.path.join(root, "src", "util.c"), "w") as f:
        f.write("int util(void){return 42;}\n")
    with open(os.path.join(root, "Makefile"), "w") as f:
        f.write("all:\n\t$(CC) $(CFLAGS) src/*.c -o app\n")


def naive_key(manifest, toolchain, params, platform):
    """The BUGGY key: the environment factor was forgotten."""
    return digest_payload({
        "v": 1,
        "source": list(manifest),
        "toolchain": toolchain.to_json(),
        "params": list(params),
        "platform": platform.to_json(),
    })


def main():
    tmp = tempfile.mkdtemp(prefix="cachekey_test_")
    try:
        dir_a = os.path.join(tmp, "home", "alice", "proj")
        dir_b = os.path.join(tmp, "var", "tmp", "xyz123", "proj")
        make_source_tree(dir_a)
        shutil.copytree(dir_a, dir_b)

        tc = Toolchain("gcc", "13.2.0", {"target": "x86_64-linux-gnu"})
        params = ["-O2", "-Wall", "-std=c11"]
        platform = PlatformInfo("linux", "x86_64", "glibc-2.38")
        env_release = {"NDEBUG": "1", "LANG": "C.UTF-8"}
        env_debug = {"NDEBUG": "", "LANG": "C.UTF-8"}  # only env differs

        manifest_a = source_manifest(dir_a)
        manifest_b = source_manifest(dir_b)

        print("== 1. identical inputs -> identical key (cache HIT) ==")
        k1 = compute_key(manifest_a, tc, params, env_release, platform)
        k2 = compute_key(manifest_a, tc, params, env_release, platform)
        check("identical inputs give identical key", k1 == k2, f"key={k1[:16]}...")
        cache = {k1: "/artifacts/app-release"}
        check("exact-match lookup hits", lookup(cache, k2) == "/artifacts/app-release")

        print("\n== 2. only env differs -> different key (the false-hit scenario) ==")
        kn1 = naive_key(manifest_a, tc, params, platform)
        kn2 = naive_key(manifest_a, tc, params, platform)
        k3 = compute_key(manifest_a, tc, params, env_debug, platform)
        check("BUG REPRO: naive key (env factor missing) collides", kn1 == kn2,
              f"naive={kn1[:16]}... reused for NDEBUG=1 AND NDEBUG=''")
        check("full key distinguishes the two envs", k1 != k3,
              f"release={k1[:16]}... debug={k3[:16]}...")
        check("lookup of debug build misses (no false hit)", lookup(cache, k3) is None)

        print("\n== 3. only absolute path differs -> identical key ==")
        kb = compute_key(manifest_b, tc, params, env_release, platform)
        check("manifest identical across directories", manifest_a == manifest_b)
        check("key identical across directories", k1 == kb,
              f"dirA={dir_a}\n{'':>7} dirB={dir_b}\n{'':>7} keyA={k1}\n{'':>7} keyB={kb}")
        check("lookup from moved checkout hits", lookup(cache, kb) == "/artifacts/app-release")

        print("\n== 4. toolchain version change -> different key ==")
        tc_new = Toolchain("gcc", "14.1.0", {"target": "x86_64-linux-gnu"})
        k4 = compute_key(manifest_a, tc_new, params, env_release, platform)
        check("toolchain bump changes key", k1 != k4, f"gcc13={k1[:16]}... gcc14={k4[:16]}...")
        check("lookup with new toolchain misses", lookup(cache, k4) is None)

        print("\n== 5. source content change -> different key ==")
        with open(os.path.join(dir_a, "src", "util.c"), "a") as f:
            f.write("/* touched */\n")
        k5 = compute_key(source_manifest(dir_a), tc, params, env_release, platform)
        check("source edit changes key", k1 != k5)

        print("\n== 6. benchmark: 100,000 key computations ==")
        payload = build_key_payload(manifest_b, tc, params, env_release, platform)
        n = 100_000
        tracemalloc.start()
        t0 = time.perf_counter()
        acc = 0
        for i in range(n):
            payload["params"] = [*params, f"-DITER={i}"]  # force distinct payloads
            acc ^= int(compute_key(payload["source"], tc, payload["params"],
                                   env_release, platform), 16)
        dt = time.perf_counter() - t0
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        print(f"  n={n}  total={dt:.3f}s  avg={dt / n * 1e6:.2f}us/op  "
              f"throughput={n / dt:,.0f} keys/s")
        print(f"  tracemalloc peak={peak / 1024:.1f} KiB  process maxRSS={rss_kb / 1024:.1f} MiB")
        check("benchmark produced nonzero work", acc != 0)

        print(f"\n{sum(results)}/{len(results)} checks passed")
        return 0 if all(results) else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
