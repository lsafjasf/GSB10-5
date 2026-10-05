"""demo.py — 输出编号规则要求的全部对照/重排/校验/跨页数据。"""

from footnote import (
    FootnoteRegistry,
    merge_parts,
    paginate_footnotes,
    render_page,
)

LINES_PER_PAGE = 4  # 每页脚注区 4 行，author 正文 7 行 => 跨页


def build():
    reg = FootnoteRegistry()
    reg.define("author", "\n".join(f"作者简介第{i}行" for i in range(1, 8)))
    reg.define("ref-a", "文献 A，某出版社，2020。")
    reg.define("data", "数据来源：国家统计局年鉴。")
    reg.define("ref-b", "文献 B，某期刊，2021。")
    reg.add_reference("ref-a", "r1")   # 1
    reg.add_reference("author", "r2")  # 2
    reg.add_reference("ref-a", "r3")   # 1（重复）
    reg.add_reference("data", "r4")    # 3
    reg.add_reference("author", "r5")  # 2（重复）
    reg.add_reference("ref-b", "r6")   # 4
    return reg


def show(title):
    print("=" * 70)
    print(title)
    print("=" * 70)


def print_mapping(reg):
    print("编号 | key    | 正文")
    print("-" * 70)
    for num, key, text in reg.mapping_table():
        print(f" {num:<3} | {key:<6} | {text.splitlines()[0]}"
              + (" …" if "\n" in text else ""))


def print_refs(reg):
    print("引用点 | key    | 显示编号")
    print("-" * 40)
    for rid, key, num in reg.reference_table():
        print(f" {rid:<5} | {key:<6} | {num}")


def main():
    reg = build()

    show("1. 对照表（编号按引用点首次出现顺序分配，重复引用共享编号）")
    print_mapping(reg)
    print()
    print_refs(reg)

    show("2. 引用一致性校验")
    problems = reg.validate()
    print("校验结果:", "全部通过（同一 key 的所有引用点指向同一编号，编号 1..4 连续）"
          if not problems else problems)

    show("3. 跨页情形（每页脚注区 4 行；author 正文 7 行，需跨页）")
    ordered = [(num, reg.body_of(key)) for num, key, _ in reg.mapping_table()]
    pages = paginate_footnotes(ordered, LINES_PER_PAGE)
    print(f"共分出 {len(pages)} 页脚注区：\n")
    for i, page in enumerate(pages, 1):
        print(render_page(i, page))
        print()
    merged = merge_parts([p for page in pages for p in page])
    print("合并后与原始正文逐字节一致:", merged == ordered)
    print(f"author 合并还原为 {len(merged[1][1].splitlines())} 行: "
          f"{merged[1][1].splitlines()[0]} … {merged[1][1].splitlines()[-1]}")

    show("4. 删除引用点后的重排（删除 data 的唯一引用点 r4）")
    print("【重排前】")
    print_mapping(reg)
    reg.remove_reference("r4")
    reg.renumber()
    print()
    print("【重排后】data(3) 编号回收，ref-b 由 4 上移为 3，无空号")
    print_mapping(reg)
    print()
    print_refs(reg)
    print()
    print("重排后校验:", "全部通过" if not reg.validate() else reg.validate())


if __name__ == "__main__":
    main()
