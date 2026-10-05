"""10 万节点渲染基准：耗时、内存峰值（tracemalloc）、行数、宽度断言与对拍。"""

import gc
import sys
import time
import tracemalloc

from render import display_width, parse, render

LEAF_COUNT = 100_000
BRANCH = 10
WIDTH = 80


def build(n=LEAF_COUNT, branch=BRANCH):
    """构造分支度 10 的均衡嵌套 list，恰好 n 个叶子标量。"""
    counter = iter(range(n))

    def rec(size):
        if size <= branch:
            return [f"leaf-{next(counter):06d}" for _ in range(size)]
        base, extra = divmod(size, branch)
        sizes = [base + (1 if i < extra else 0) for i in range(branch)]
        return [rec(s) for s in sizes if s]

    return rec(n)


def count_nodes(node):
    if isinstance(node, list):
        return 1 + sum(count_nodes(x) for x in node)
    return 1


def main():
    root = build()
    total = count_nodes(root)
    print(f"叶子标量: {LEAF_COUNT:,}  含容器总节点数: {total:,}  最大深度: 6  width={WIDTH}")

    gc.collect()
    tracemalloc.start()
    t0 = time.perf_counter()
    text = render(root, width=WIDTH)
    t1 = time.perf_counter()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    lines = text.splitlines()
    over = [(i, l) for i, l in enumerate(lines, 1) if display_width(l) > WIDTH]
    print(f"渲染耗时:   {t1 - t0:.3f} s")
    print(f"渲染内存峰值(tracemalloc): {peak / 1024 / 1024:.1f} MiB")
    print(f"输出物理行: {len(lines):,}  输出字节: {len(text.encode()):,}")
    print(f"宽度断言:   {'PASS（所有行 <= ' + str(WIDTH) + '）' if not over else 'FAIL: ' + str(over[:3])}")
    assert not over

    t0 = time.perf_counter()
    back = parse(text)
    t1 = time.perf_counter()
    print(f"解析耗时:   {t1 - t0:.3f} s")
    print(f"结构对拍:   {'PASS（parse(render(x)) == x）' if back == root else 'FAIL'}")
    assert back == root
    return 0


if __name__ == "__main__":
    sys.exit(main())
