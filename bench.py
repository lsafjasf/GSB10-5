"""10 万段落规范化性能基准：耗时与内存峰值。

运行: python3 bench.py
"""

import gc
import time
import tracemalloc

import mdnorm

N_PARAGRAPHS = 100_000


def build_doc(n):
    blocks = []
    for i in range(n):
        lines = [
            f"段落{i} 开头   有多余空格",
            f"第二行\t软换行  拼接",
            f"第三行硬换行  ",
            f"末行反斜杠硬换行\\",
        ]
        blocks.append("\n".join(lines) + "\n")
        if i % 7 == 0:
            blocks.append("```python\n"
                          "def g( ):\n"
                          "    x = 1  \t  # 代码块原样\n"
                          "    return   x\n"
                          "```\n")
        if i % 11 == 0:
            blocks.append("> 引用  行\n> 引用二行\n")
        blocks.append("\n\n")
    return "".join(blocks)


def main():
    doc = build_doc(N_PARAGRAPHS)
    size_mb = len(doc.encode("utf-8")) / 1024 / 1024
    print(f"输入: {N_PARAGRAPHS} 段落, {len(doc.splitlines()):,} 行, "
          f"{size_mb:.1f} MiB (UTF-8)")

    def run(collect_diffs, trace):
        gc.collect()
        if trace:
            tracemalloc.start()
        t0 = time.perf_counter()
        result, diffs = mdnorm.normalize(doc, collect_diffs=collect_diffs)
        elapsed = time.perf_counter() - t0
        peak = 0
        if trace:
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
        return result, diffs, elapsed, peak

    # 计时与内存分开测：tracemalloc 会拖慢执行，混测会高估耗时
    result, diffs, elapsed, _ = run(True, trace=False)
    _, _, elapsed_fast, _ = run(False, trace=False)
    _, _, _, peak = run(True, trace=True)
    _, _, _, peak_fast = run(False, trace=True)
    result_fast, _, _, _ = run(False, trace=False)
    assert result == result_fast, "两种模式输出必须一致"

    records = mdnorm.code_region_hashes(doc, result)
    assert all(r["identical"] for r in records)

    print()
    print(f"{'模式':<22}{'耗时':>10}{'吞吐(段落/s)':>14}{'内存峰值':>12}")
    for name, el, pk in (("含差异清单", elapsed, peak),
                         ("仅规范化", elapsed_fast, peak_fast)):
        print(f"{name:<22}{el:>8.3f}s{N_PARAGRAPHS / el:>14,.0f}"
              f"{pk / 1024 / 1024:>10.1f}MiB")
    print()
    print(f"差异条目    : {len(diffs):,} 条")
    print(f"代码块校验  : {len(records):,} 个, 全部逐字节相同")
    print(f"结果大小    : {len(result.encode('utf-8')) / 1024 / 1024:.1f} MiB")


if __name__ == "__main__":
    main()
