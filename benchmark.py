"""benchmark.py — 5 万个引用点的处理耗时与内存峰值（仅标准库）。"""

import random
import time
import tracemalloc

from footnote import FootnoteRegistry, paginate_footnotes, merge_parts

N_REFS = 50_000      # 引用点总数
N_KEYS = 5_000       # 不同脚注条数（大量重复引用）
N_BODY_LINES = 20    # 每条正文行数，分页时制造跨页
PAGE_LINES = 40


def main():
    rng = random.Random(42)

    tracemalloc.start()
    t0 = time.perf_counter()

    reg = FootnoteRegistry()
    for k in range(N_KEYS):
        reg.define(f"fn{k}", "\n".join(f"第{k}条正文第{i}行" for i in range(N_BODY_LINES)))

    for i in range(N_REFS):                       # 5 万引用点，随机指向各脚注
        reg.add_reference(f"fn{rng.randrange(N_KEYS)}", f"r{i}")

    t_add = time.perf_counter() - t0

    t1 = time.perf_counter()
    reg.renumber()                                 # 首次编号
    t_renumber = time.perf_counter() - t1

    t2 = time.perf_counter()
    problems = reg.validate()                      # 引用一致性校验
    t_validate = time.perf_counter() - t2

    t3 = time.perf_counter()
    ordered = [(num, reg.body_of(key)) for num, key, _ in reg.mapping_table()]
    pages = paginate_footnotes(ordered, PAGE_LINES)   # 跨页拆分
    restored = merge_parts([p for page in pages for p in page])
    assert restored == ordered                     # 合并无损
    t_page = time.perf_counter() - t3

    # 模拟删除 10% 引用点后重排
    t4 = time.perf_counter()
    for i in range(0, N_REFS, 10):
        reg.remove_reference(f"r{i}")
    reg.renumber()
    t_delete_renumber = time.perf_counter() - t4

    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print(f"引用点总数        : {N_REFS}（其中不同脚注 {N_KEYS} 条，存在大量重复引用）")
    print(f"删除引用点数      : {N_REFS // 10}")
    print(f"存活引用点数      : {N_REFS - N_REFS // 10}")
    print(f"重排后持有编号数  : {len(reg.mapping_table())}")
    print(f"校验问题          : {problems if problems else '无（全部通过）'}")
    print(f"分页结果          : {len(pages)} 页，合并后与原文一致")
    print("-" * 50)
    print(f"定义+追加 5 万引用点 : {t_add * 1000:8.1f} ms")
    print(f"首次编号 renumber   : {t_renumber * 1000:8.1f} ms")
    print(f"一致性校验 validate  : {t_validate * 1000:8.1f} ms")
    print(f"跨页拆分+合并        : {t_page * 1000:8.1f} ms")
    print(f"删除 5 千点后重排    : {t_delete_renumber * 1000:8.1f} ms")
    print(f"总计（不含进程启动） : "
          f"{(t_add + t_renumber + t_validate + t_page + t_delete_renumber) * 1000:8.1f} ms")
    print(f"内存峰值（tracemalloc）: {peak / 1024 / 1024:6.1f} MiB")


if __name__ == "__main__":
    main()
