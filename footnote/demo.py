"""demo.py: 生成 REPORT.md —— 对照表、跨页结果、删除重排、一致性校验、性能。"""

import os
import random
import resource
import sys
import time
import tracemalloc

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from footnote_lib import (
    FootnoteDef, Line, Ref, assign_numbers, check_consistency, paginate,
)

OUT = []


def w(text=""):
    OUT.append(text)


def render_pages(pages):
    for page in pages:
        w(f"---- 第 {page.number}页 " + "-" * 36)
        w("| 正文区:")
        for line in page.body:
            w(f"|   {line}")
        w("| 页脚区:")
        for line in page.foot:
            w(f"|   {line}")
        w("+" + "-" * 46)


def demo_book():
    defs = {
        "src": FootnoteDef("src", ["《脚注排版考》，某某出版社，2020。"]),
        "auth": FootnoteDef("auth", ["张三，《引文研究》，第 12 页。"]),
        "see": FootnoteDef("see", ["另见李四《排版史话》第三章。"]),
        "note": FootnoteDef("note", ["此说尚有争议，姑录备考。"]),
        "long": FootnoteDef("long", [f"长注：第{i}行，内容较长用于演示跨页拆分。" for i in range(1, 9)]),
    }
    lines = [
        Line("引文一", [Ref("src")]),
        Line("引文二", [Ref("auth")]),
        Line("引文三", [Ref("src")]),
        Line("引文四", [Ref("src"), Ref("src", tag="b")]),
        Line("补充说明", [Ref("see")]),
        Line("旁证", [Ref("note")]),
        Line("长注引出", [Ref("long")]),
        Line("正文续一"),
        Line("正文续二"),
        Line("正文续三"),
        Line("正文续四"),
        Line("正文续五"),
    ]
    return lines, defs


def main():
    lines, defs = demo_book()
    body_cap, foot_cap = 4, 8

    w("# 脚注编号与跨页合并 —— 运行报告")
    w()
    w("环境: Python " + sys.version.split()[0] + "（仅标准库）")
    w()

    # ---------- 1. 编号对照表 ----------
    pages, numbers, points = paginate(lines, defs, body_cap, foot_cap)
    w("## 1. 编号对照表（重复引用共享编号）")
    w()
    w("编号按 key 的**首次引用点**在全文中的顺序分配；同一 key 的重复引用共享同一编号。")
    w()
    w("| 编号 | 脚注 key | 引用点 seq（全文顺序） | 引用位置 |")
    w("|---|---|---|---|")
    by_key = {}
    for p in points:
        by_key.setdefault(p.key, []).append(p)
    line_page = {}
    li = 0
    for page in pages:
        for _ in page.body:
            line_page[li] = page.number
            li += 1
    for key in sorted(numbers, key=numbers.get):
        pts = by_key[key]
        seqs = ", ".join(f"#{p.seq}" for p in pts)
        locs = ", ".join(f"p{line_page[p.line_index]}.L{p.line_index + 1}" for p in pts)
        w(f"| {numbers[key]} | {key} | {seqs} | {locs} |")
    w()
    w(f"共 {len(points)} 个引用点，{len(numbers)} 个编号 —— 重复引用不另占编号。")
    w()

    # ---------- 2. 分页与跨页结果 ----------
    w("## 2. 分页结果（含跨页拆分/续块）")
    w()
    w(f"页面参数: 正文区 {body_cap} 行/页，页脚区 {foot_cap} 行/页。")
    w()
    render_pages(pages)
    w()
    w("跨页情形：")
    for page in pages:
        for ch in page.chunks:
            if ch.continuation or ch.has_more or ch.carried:
                kind = []
                if ch.has_more:
                    kind.append("本页排不下、余下顺延")
                if ch.continuation:
                    kind.append("上一页续块")
                if ch.carried:
                    kind.append("引用页放不下、整体顺延")
                w(f"- 第{page.number}页 [{ch.number}] {ch.key}: 正文第{ch.start + 1}~{ch.start + len(ch.lines)}行（{'、'.join(kind)}）")
    w()
    long_chunks = [c for pg in pages for c in pg.chunks if c.key == "long"]
    merged = [t for c in long_chunks for t in c.lines]
    w(f"跨页合并校验：脚注 [long] 的 {len(long_chunks)} 个块合并后 == 定义正文 → **{merged == defs['long'].body}**")
    w()

    # ---------- 3. 删除引用后重排 ----------
    w("## 3. 删除引用点后的编号重排")
    w()
    deleted_line = 2  # “引文三”(src) —— src 仍有其他引用，编号不变
    deleted_line2 = 4  # “补充说明”(see) —— see 的唯一引用被删，后续重排
    w(f"删除第{deleted_line + 1}行（src 的重复引用之一）与第{deleted_line2 + 1}行（see 的唯一引用）。")
    w()
    lines2 = [l for i, l in enumerate(lines) if i not in (deleted_line, deleted_line2)]
    pages2, numbers2, points2 = paginate(lines2, defs, body_cap, foot_cap)
    w("| 脚注 key | 删除前编号 | 删除后编号 | 说明 |")
    w("|---|---|---|---|")
    for key in sorted(set(numbers) | set(numbers2), key=lambda k: numbers.get(k, 99)):
        before = numbers.get(key, "—")
        after = numbers2.get(key, "（无引用，不编号）")
        note = ""
        if key == "src":
            note = "删的是重复引用，仍有剩余引用，编号不变"
        elif key == "see":
            note = "唯一引用被删，定义不再编号"
        elif before != after:
            note = "前移补位，不留空号"
        w(f"| {key} | {before} | {after} | {note} |")
    w()
    w(f"删除前: {dict(sorted(numbers.items(), key=lambda kv: kv[1]))}")
    w()
    w(f"删除后: {dict(sorted(numbers2.items(), key=lambda kv: kv[1]))}")
    w()
    w("删除后分页结果：")
    w()
    render_pages(pages2)
    w()

    # ---------- 4. 引用一致性校验 ----------
    w("## 4. 引用一致性校验")
    w()
    for label, (pgs, nums, pts) in {
        "删除前": (pages, numbers, points),
        "删除后": (pages2, numbers2, points2),
    }.items():
        problems = check_consistency(pgs, defs, pts, nums, foot_cap=foot_cap)
        w(f"- {label}: {len(pts)} 个引用点 / {len(nums)} 个编号 → "
          + ("**全部通过**（每个引用点都能指回同一编号，无空号，跨页块连续）"
             if not problems else f"发现问题: {problems}"))
    w()
    w("逐点校验明细（删除前）：")
    w()
    w("| 引用点 seq | key | 正文编号 | 页脚条目编号 | 回链包含该点 |")
    w("|---|---|---|---|---|")
    chunk_refs = {}
    chunk_num = {}
    for pg in pages:
        for c in pg.chunks:
            chunk_num[c.key] = c.number
            for (_pg2, _ln, seq) in c.refs:
                chunk_refs[seq] = c.key
    for p in points:
        ok_num = chunk_num.get(p.key) == numbers[p.key]
        ok_back = p.seq in chunk_refs
        w(f"| #{p.seq} | {p.key} | {numbers[p.key]} | {chunk_num.get(p.key)} | {'✓' if ok_back and ok_num else '✗'} |")
    w()

    # ---------- 5. 性能：5 万引用点 ----------
    w("## 5. 性能：50,000 个引用点")
    w()
    rng = random.Random(42)
    n_defs = 4000
    big_defs = {
        f"k{i}": FootnoteDef(f"k{i}", [f"脚注{i} 第{j}行。" for j in range(rng.randint(1, 3))])
        for i in range(n_defs)
    }
    keys = list(big_defs)
    big_lines = []
    total_refs = 0
    while total_refs < 50000:
        refs = [Ref(rng.choice(keys)) for _ in range(rng.randint(1, 3))]
        total_refs += len(refs)
        big_lines.append(Line(f"正文行{len(big_lines) + 1}", refs))
    big_lines[-1].refs = big_lines[-1].refs[:len(big_lines[-1].refs) - (total_refs - 50000)]
    total_refs = sum(len(l.refs) for l in big_lines)

    # 计时（不含内存追踪开销）
    t0 = time.perf_counter()
    b_pages, b_numbers, b_points = paginate(big_lines, big_defs, body_cap=40, foot_cap=20)
    t1 = time.perf_counter()
    # 测内存（tracemalloc 会拖慢执行，单独再跑一次）
    tracemalloc.start()
    paginate(big_lines, big_defs, body_cap=40, foot_cap=20)
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    b_problems = check_consistency(b_pages, big_defs, b_points, b_numbers, foot_cap=20)
    w(f"- 引用点: {total_refs}，脚注定义: {n_defs}，正文行: {len(big_lines)}")
    w(f"- 结果: {len(b_pages)} 页，{len(b_numbers)} 个编号，一致性校验 {'通过' if not b_problems else b_problems[:3]}")
    w(f"- 耗时: **{(t1 - t0) * 1000:.1f} ms**（编号 + 分页 + 页脚排版）")
    w(f"- 内存峰值（tracemalloc）: **{peak / 1024 / 1024:.1f} MiB**；进程 RSS 峰值: {rss_kb / 1024:.1f} MiB")
    w()

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "REPORT.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(OUT) + "\n")
    print(f"written {out_path}")


if __name__ == "__main__":
    main()
