# -*- coding: utf-8 -*-
"""演示：四个场景生成目录、分页明细与逐项校验数据，写入 out/。

运行：python3 main.py
"""
import os

from tocgen import (
    Document, Heading, Paragraph, Layout,
    paginate, build_toc, render_toc, validate, format_validation,
    logical_label,
)

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
L = Layout(lines_per_page=12, page_width=48, keep_with_next=2)
W = 48


def page_dump(p):
    lines = []
    for pg in p.pages:
        label = logical_label(pg.number, p.front_pages)
        lines.append(f"── 物理页 {pg.number}（印出页码 {label}）" + "─" * 20)
        lines += pg.lines
    return "\n".join(lines)


def emit(name, doc, layout=L, include_front=True):
    p = paginate(doc, layout)
    entries = build_toc(doc, p, include_front=include_front)
    toc = render_toc(entries, width=W)
    rows = validate(doc, layout, p, entries)
    with open(os.path.join(OUT, f"{name}.txt"), "w", encoding="utf-8") as f:
        f.write(f"### 场景：{name}\n\n[目录]\n{toc}\n\n[分页明细]\n{page_dump(p)}\n\n"
                f"[逐项校验]\n{format_validation(rows)}\n")
    print(f"{name}: {len(entries)} 项, {len(p.pages)} 物理页, "
          f"校验 {'全部通过' if all(r.label_ok and r.found_on_page for r in rows) else '存在失败'}")


def main():
    os.makedirs(OUT, exist_ok=True)

    emit("1_no_front", Document(body=[
        Heading(1, "第一章 起步"),
        Paragraph("本章介绍基本概念。" * 6),
        Heading(2, "第一节 术语"),
        Paragraph("术语解释。" * 10),
        Heading(1, "第二章 深入"),
        Paragraph("深入讨论。" * 12),
        Heading(1, "第三章 收束"),
        Paragraph("总结。" * 4),
    ]), include_front=False)

    emit("2_with_front", Document(
        front=[
            Heading(1, "序言"),
            Paragraph("写在前面的话。" * 8),
            Heading(1, "凡例"),
            Paragraph("体例说明。" * 5),
        ],
        body=[
            Heading(1, "第一章 总论"),
            Paragraph("总论内容。" * 10),
            Heading(2, "第一节 背景"),
            Paragraph("背景介绍。" * 8),
            Heading(1, "第二章 分论"),
            Paragraph("分论内容。" * 6),
        ]))

    long_title = ("论在一个标题长度远远超过页面可用宽度以至于必须折成多行"
                  "才能排下的情况下目录页码列如何保持严格对齐而不发生任何错位")
    emit("3_long_title", Document(body=[
        Heading(1, long_title),
        Paragraph("正文。" * 20),
        Heading(2, "普通的短节标题"),
        Paragraph("内容。" * 6),
        Heading(1, "第三章 也是正常长度"),
        Paragraph("收尾。" * 6),
    ]), include_front=False)

    mixed = Layout(lines_per_page=12, page_width=48, keep_with_next=2,
                   chapter_new_page=False)
    emit("4_mixed_levels_same_page", Document(body=[
        Heading(1, "甲章"),
        Paragraph("甲章正文。" * 3),
        Heading(1, "乙章"),          # 与甲章末尾同页
        Heading(2, "乙章第一节"),
        Paragraph("乙一。" * 10),
        Heading(3, "乙章第一节之一"),
        Paragraph("细节。" * 8),
        Heading(2, "乙章第二节"),
        Paragraph("乙二。" * 14),
        Heading(1, "丙章"),
        Paragraph("丙。" * 5),
    ]), layout=mixed, include_front=False)


if __name__ == "__main__":
    main()
