"""自测：完全相同输入 / 仅环境不同 / 仅路径不同 / 工具链版本变化 / 错误命中复现。

运行: python3 tests/test_buildcache.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from buildcache import BuildInputs, compute_key, lookup, select_env

PLATFORM = BuildInputs.detect_platform()


def make_inputs(root, env=None, toolchain=None, params=None):
    """从目录扫描源码（键只用相对路径+内容，root 不进入键）。"""
    sources = {}
    for dirpath, _dirs, files in os.walk(root):
        for f in sorted(files):
            full = os.path.join(dirpath, f)
            rel = os.path.relpath(full, root)
            with open(full, "rb") as fh:
                sources[rel] = fh.read()
    return BuildInputs(
        sources=sources,
        toolchain=toolchain or {"gcc": "13.2.0"},
        params=params or {"opt": "-O2"},
        env=env if env is not None else select_env(os.environ),
        platform_info=PLATFORM,
    )


def write_tree(root, files):
    for rel, content in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(content)


FILES = {"main.c": b"int main(){return 0;}\n", "lib/util.c": b"int u(){return 1;}\n"}

passed = 0


def check(name, cond, detail=""):
    global passed
    assert cond, f"FAIL: {name} {detail}"
    passed += 1
    print(f"  PASS  {name}  {detail}")


def main():
    with tempfile.TemporaryDirectory() as td:
        dir_a = os.path.join(td, "work", "proj")
        dir_b = os.path.join(td, "other", "deeper", "proj")
        write_tree(dir_a, FILES)
        write_tree(dir_b, FILES)

        print("== 1. 完全相同的输入 ==")
        k1 = compute_key(make_inputs(dir_a))
        k2 = compute_key(make_inputs(dir_a))
        check("相同输入键相同", k1 == k2, f"key={k1[:16]}…")

        print("== 2. 仅绝对路径不同 ==")
        k_b = compute_key(make_inputs(dir_b))
        check("不同绝对目录键不变", k1 == k_b,
              f"dirA={k1[:16]}… dirB={k_b[:16]}…")

        print("== 3. 仅环境变量不同（CFLAGS -O2 vs -O0）==")
        env_o2 = {"CFLAGS": "-O2"}
        env_o0 = {"CFLAGS": "-O0"}
        k_o2 = compute_key(make_inputs(dir_a, env=env_o2))
        k_o0 = compute_key(make_inputs(dir_a, env=env_o0))
        check("环境不同键不同", k_o2 != k_o0,
              f"O2={k_o2[:16]}… O0={k_o0[:16]}…")

        print("== 4. 工具链版本变化（gcc 13.2.0 -> 14.1.0）==")
        k_g14 = compute_key(make_inputs(dir_a, toolchain={"gcc": "14.1.0"}))
        check("工具链不同键不同", k1 != k_g14,
              f"13.2={k1[:16]}… 14.1={k_g14[:16]}…")

        print("== 5. 错误命中复现：键少算环境因素 ==")
        # 构建 A：CFLAGS=-O2，产物缓存
        cache = {}
        key_naive_o2 = compute_key(make_inputs(dir_a, env=env_o2), include_env=False)
        cache[key_naive_o2] = "artifact-built-with-O2"
        # 构建 B：CFLAGS=-O0，输入看似相同（源码/参数/工具链一致）
        key_naive_o0 = compute_key(make_inputs(dir_a, env=env_o0), include_env=False)
        check("缺陷键发生碰撞（错误命中）", key_naive_o2 == key_naive_o0,
              f"naive={key_naive_o2[:16]}… -> 会错拿 O2 产物")
        check("正确键区分两者", k_o2 != k_o0, "env 入键后无碰撞")

        print("== 6. 命中 / 未命中判定 ==")
        good_cache = {k_o2: "artifact-O2"}
        _, hit = lookup(good_cache, make_inputs(dir_a, env=env_o2))
        _, miss = lookup(good_cache, make_inputs(dir_a, env=env_o0))
        check("同环境 HIT", hit == "HIT")
        check("异环境 MISS", miss == "MISS")

        print("== 7. 源码内容变化 ==")
        write_tree(dir_b, {**FILES, "main.c": b"int main(){return 1;}\n"})
        k_changed = compute_key(make_inputs(dir_b))
        check("源码变化键不同", k1 != k_changed)

        print("== 8. 参数顺序无关（规范化）==")
        ka = compute_key(make_inputs(dir_a, params={"a": "1", "b": "2"}))
        kb = compute_key(make_inputs(dir_a, params={"b": "2", "a": "1"}))
        check("参数顺序不影响键", ka == kb)

    print(f"\n全部通过: {passed} 项")


if __name__ == "__main__":
    main()
