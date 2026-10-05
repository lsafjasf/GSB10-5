"""demo.py —— 四种情形的目录成品、逐项校验数据与对齐结果演示。

运行：python3 demo.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from toc_lib import (  # noqa: E402
    Heading, LayoutConfig, Paragraph, Section,
    build, check_toc_layout, display_width, verify,
)


def cjk_para(lines, width):
    return Paragraph("文" * (width // 2 * lines))


def show_toc(res):
    for line in res.toc_lines:
        print("  |" + line + "|")


def show_verify(res, limit=None):
    rows = verify(res)
    print(f"  {'标题':<28} {'级':>2} {'物理页':>4} {'落页页码':>6} "
          f"{'目录页码':>6}  一致")
    shown = rows if limit is None else rows[:limit]
    for row in shown:
        title = row.title if display_width(row.title) <= 26 \
            else row.title[:12] + "…"
        print(f"  {title:<28} {row.level:>2} {row.physical:>4} "
              f"{row.label_in_page:>6} {row.label_in_toc:>6}  "
              f"{'OK' if row.ok else 'MISMATCH'}")
    if limit is not None and len(rows) > limit:
        print(f"  … 共 {len(rows)} 项，其余略")
    bad = [r for r in rows if not r.ok]
    print(f"  => 逐项校验：{len(rows) - len(bad)}/{len(rows)} 一致"
          + ("，全部通过" if not bad else f"，{len(bad)} 项不一致！"))


def show_layout(res):
    check = check_toc_layout(res)
    cols = sorted(set(check.label_line_columns))
    print(f"  => 目录排版：最大行宽 {check.max_line_width}/栏宽 "
          f"{check.width_limit}；页码行右边界列集合 {cols}"
          f"（统一={check.label_lines_right_aligned}）")


def hr(title):
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def scenario_no_front():
    hr("情形 A：无前置内容（物理页 == 逻辑页，阿拉伯数字从 1 起）")
    cfg = LayoutConfig(page_height=10, page_width=40)
    doc = [
        Section("body", [Heading(1, "第一章 概述"), cjk_para(3, 40),
                         Heading(2, "1.1 背景"), cjk_para(2, 40)]),
        Section("body", [Heading(1, "第二章 方法"), cjk_para(4, 40),
                         Heading(2, "2.1 模型"), cjk_para(2, 40)]),
    ]
    res = build(doc, cfg)
    print("目录成品（栏宽 40）：")
    show_toc(res)
    show_verify(res)
    show_layout(res)


def scenario_with_front():
    hr("情形 B：有前置内容（扉页 i、目录 ii、前言 iii…，正文重起 1）")
    cfg = LayoutConfig(page_height=12, page_width=40)
    doc = [
        Section("front", [Paragraph("扉页"), cjk_para(2, 40)], name="扉页"),
        Section("toc", [], name="目录"),
        Section("front", [Heading(1, "前言"), cjk_para(3, 40)],
                include_in_toc=True, name="前言"),
        Section("body", [Heading(1, "第一章 概述"), cjk_para(3, 40),
                         Heading(2, "1.1 背景"), cjk_para(2, 40)]),
        Section("body", [Heading(1, "第二章 方法"), cjk_para(2, 40)]),
    ]
    res = build(doc, cfg)
    print(f"全书物理页 {res.total_pages}，目录占 {res.toc_page_count} 页，"
          f"不动点迭代 {res.iterations} 轮收敛")
    print("各节首页（物理页 -> 逻辑页码）：")
    for si, (sec, pages) in enumerate(res.sections):
        first_phys = sum(len(p) for _, p in res.sections[:si]) + 1
        label = res.page_labels[(si, 0)]
        kind = {"front": "前置节", "body": "正文节", "toc": "目录节"}[sec.kind]
        print(f"  节{si}（{kind}{sec.name}）：物理页 {first_phys} -> {label}")
    print("目录成品（栏宽 40）：")
    show_toc(res)
    show_verify(res)
    show_layout(res)


def scenario_chapter_edge():
    hr("情形 C：章末/章首同页与换页时页码取值（确定性规则）")
    cfg = LayoutConfig(page_height=20, page_width=40)
    doc = [Section("body", [Heading(1, "第一章"), cjk_para(3, 40)]),
           Section("body", [Heading(1, "第二章"), cjk_para(3, 40)])]
    res = build(doc, cfg)
    p1 = res.heading_pages[id(doc[0].blocks[0])]
    p2 = res.heading_pages[id(doc[1].blocks[0])]
    print(f"默认（章级强制换页）：第一章末仅排到第 1 页第 4 行，"
          f"第二章仍另起新页 -> 第一章 {p1}，第二章 {p2}")

    cfg2 = LayoutConfig(page_height=20, page_width=40,
                        chapter_break_level=0)
    doc2 = [Section("body", [Heading(1, "第一章"), cjk_para(3, 40),
                             Heading(1, "第二章"), cjk_para(3, 40)])]
    res2 = build(doc2, cfg2)
    q1 = res2.heading_pages[id(doc2[0].blocks[0])]
    q2 = res2.heading_pages[id(doc2[0].blocks[2])]
    print(f"关闭强制换页（同一正文节内）：章首接续章末，"
          f"同页取共同页 -> 第一章 {q1}，第二章 {q2}")

    cfg3 = LayoutConfig(page_height=6, page_width=40, keep_with_next=1)
    doc3 = [Section("body", [cjk_para(5, 40), Heading(2, "页底标题"),
                             cjk_para(2, 40)])]
    res3 = build(doc3, cfg3)
    q3 = res3.heading_pages[id(doc3[0].blocks[1])]
    print(f"页底保护：页高 6 已排 5 行，标题+1 行正文放不下，"
          f"标题整体下推 -> 页底标题 {q3}")


def scenario_long_title():
    hr("情形 D：超长标题 + 多级混排（缩进与页码对齐不错位）")
    cfg = LayoutConfig(page_height=60, page_width=40)
    long_title = ("目录页码对齐超长标题测试：当一个章节标题的长度超过栏宽"
                  "时必须按显示宽度折行，续行使用悬挂缩进，页码仍然与"
                  "其他条目对齐于同一右边界 internationalization")
    doc = [Section("body", [
        Heading(1, "第一章"),
        Heading(2, "1.1 二级标题"),
        Heading(3, "1.1.1 三级标题"),
        Heading(1, long_title),
        cjk_para(2, 40),
    ])]
    res = build(doc, cfg)
    print("目录成品（栏宽 40，竖线为栏宽边界）：")
    show_toc(res)
    show_verify(res)
    show_layout(res)


if __name__ == "__main__":
    scenario_no_front()
    scenario_with_front()
    scenario_chapter_edge()
    scenario_long_title()
