"""10 万行基准文档对生成（完全确定，无随机）。

v1：625 章 x 3 节，每节 1 标题 + 52 行正文 = 53 行；
    625 * (1 + 3*53) = 625 * 160 = 100,000 行。
v2 在 v1 基础上施加：20 章仅改名、1 章整章移动、1 章改名+移动、
    3 节同父降级、2 节并入他节、10 章内容修改（每节连续改写 10/52 行）、
    5 章删除、5 章新增。行数保持 100,000。
"""
from __future__ import annotations

import copy
import os
from typing import Dict, List

CHAPTERS = 625
SECTIONS = 3
BODY = 52


def _body(c: int, s: int) -> List[str]:
    return [f"第{c}章第{s}节第{i}行 内容种子{c * 10000 + s * 100 + i} 填充说明文字"
            for i in range(BODY)]


def _chapter(c: int) -> dict:
    return {
        "title": f"第{c}章 主题{c}",
        "sections": [{"title": f"第{c}章第{s}节", "level": 2,
                      "body": _body(c, s), "children": []}
                     for s in range(SECTIONS)],
    }


def _render_sec(sec: dict, out: List[str]) -> None:
    out.append("#" * sec["level"] + " " + sec["title"])
    out.extend(sec["body"])
    for child in sec["children"]:
        _render_sec(child, out)


def render(chapters: List[dict]) -> str:
    out: List[str] = []
    for ch in chapters:
        out.append("# " + ch["title"])
        for sec in ch["sections"]:
            _render_sec(sec, out)
    return "\n".join(out) + "\n"


def generate() -> tuple:
    chapters = [_chapter(c) for c in range(CHAPTERS)]
    ch2 = copy.deepcopy(chapters)

    def find(title: str) -> int:
        return next(i for i, ch in enumerate(ch2) if ch["title"] == title)

    # 1) 仅改名：第 10..29 章
    for c in range(10, 30):
        ch2[find(f"第{c}章 主题{c}")]["title"] = f"第{c}章 议题{c}"
    # 2) 整章移动：第 100 章移到第 300 章之后
    ch = ch2.pop(find("第100章 主题100"))
    ch2.insert(find("第300章 主题300") + 1, ch)
    # 3) 改名 + 移动：第 200 章
    ch = ch2.pop(find("第200章 主题200"))
    ch["title"] = "第200章 议题200（修订）"
    ch2.insert(find("第350章 主题350") + 1, ch)
    # 4) 层级调整（同父降级）：第 400 章全部小节
    for sec in ch2[find("第400章 主题400")]["sections"]:
        sec["level"] = 3
    # 5) 层级调整（并入他节）：第 410 章后两节并入第一节
    secs = ch2[find("第410章 主题410")]["sections"]
    secs[1]["level"] = 3
    secs[2]["level"] = 3
    secs[0]["children"] = [secs[1], secs[2]]
    del secs[1:]
    # 6) 内容修改：第 500..509 章，每节连续改写 10 行
    for c in range(500, 510):
        for sec in ch2[find(f"第{c}章 主题{c}")]["sections"]:
            sec["body"][10:20] = [f"第{c}章改写第{i}行 全新内容" for i in range(10)]
    # 7) 删除：第 550..554 章
    for c in range(550, 555):
        del ch2[find(f"第{c}章 主题{c}")]
    # 8) 新增：第 900..904 章，插到第 600 章之后
    new_chapters = [_chapter(900 + k) for k in range(5)]
    for ch in new_chapters:
        ch["title"] = ch["title"].replace("主题", "新增主题")
    idx = find("第600章 主题600") + 1
    ch2[idx:idx] = new_chapters

    return render(chapters), render(ch2)


def main() -> None:
    old_text, new_text = generate()
    os.makedirs("bench", exist_ok=True)
    for name, text in (("v1.md", old_text), ("v2.md", new_text)):
        path = os.path.join("bench", name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"{path}: {len(text.splitlines())} 行, {len(text.encode('utf-8'))} 字节")


if __name__ == "__main__":
    main()
