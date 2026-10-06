"""生成验证报告 REPORT.md：python3 -m mdws.report"""

import hashlib
import io
import platform
import sys

from mdws import bench, normalize as nz

SAMPLE = (
    "   纯正文段落，   含多余    空白。 \n"
    "\t \n"
    "第二段行尾是硬换行标记（3 个空格）：   \n"
    "软换行后的下一行。\n"
    "\n"
    ">   引用块第一行，  空白被折叠。  \n"
    "> 引用块第二行（行数与 > 标记必须保留）。 \n"
    ">\n"
    "\n"
    "```python\n"
    "def   f( x ):\n"
    "\treturn   x    \n"
    "```\n"
    "\n"
    "    缩进代码块   原样保留  \n"
    "\t包括制表符\t\n"
    "\n"
    "-   列表项   间距\n"
    "结尾 CRLF 行。\r\n"
)

HARD_SOFT = [
    ("硬换行（恰好 2 空格）", "foo  \nbar\n"),
    ("硬换行（5 空格+制表符）", "foo     \t\nbar\n"),
    ("软换行（行尾 1 空格）", "foo \nbar\n"),
    ("软换行（无行尾空白）", "foo\nbar\n"),
]


def _vis(s: str) -> str:
    return ("`" + s.replace("`", "\\`")
            .replace("\t", "→").replace("\r", "␍")
            .replace("\n", "␊") + "`")


def build() -> str:
    out = io.StringIO()
    w = out.write

    res = nz.normalize(SAMPLE)

    w("# 规范化验证报告\n\n")
    w("本报告由 `python3 -m mdws.report` 自动生成。\n\n")

    w("## 1. 差异清单（示例文档逐条解释）\n\n")
    w("示例文档覆盖：纯正文、围栏代码块、缩进代码块、引用块、硬换行、"
      "软换行、空行、列表项、CRLF。\n\n")
    w("| 行号 | 触发规则 | 改前 | 改后 | 解释 |\n")
    w("|---|---|---|---|---|\n")
    explain = {
        nz.R_EOL: "CRLF 统一为 LF",
        nz.R_BLANK: "纯空白行清空为空行",
        nz.R_TRIM: "软换行前的零散行尾空白删除",
        nz.R_HARD: "硬换行标记规范为恰好两个空格",
        nz.R_COLLAPSE: "行内连续空白折叠为单个空格",
        nz.R_LEAD: "段首无意义前导空白删除",
        nz.R_QUOTE: "引用标记 '> ' 间距规范化",
        nz.R_LIST: "列表标记后间距规范化",
    }
    for c in res.changes:
        rules = ", ".join(c.rules)
        why = "；".join(dict.fromkeys(explain[r] for r in c.rules))
        w(f"| {c.line} | {rules} | {_vis(c.before)} | {_vis(c.after)} | {why} |\n")
    w(f"\n共 {len(res.changes)} 处差异，行数不变"
      f"（{SAMPLE.count(chr(10))} 行 -> {res.text.count(chr(10))} 行）。\n\n")

    w("## 2. 硬换行 vs 软换行对照\n\n")
    w("| 情形 | 输入 | 输出 | 处理 |\n")
    w("|---|---|---|---|\n")
    for name, src in HARD_SOFT:
        r = nz.normalize(src)
        if r.changes:
            action = "；".join(dict.fromkeys(
                explain[rule] for rule in r.changes[0].rules))
        else:
            action = "原样保留"
        w(f"| {name} | {_vis(src)} | {_vis(r.text)} | {action} |\n")
    w("\n判定依据：行尾连续空白中空格数 ≥ 2 视为 Markdown 硬换行标记，"
      "规范为恰好两个空格；否则视为软换行前的冗余空白，删除。"
      "两种情况下换行符本身（软换行）都保留。\n\n")

    w("## 3. 代码块逐字节不变验证\n\n")
    before = nz.protected_lines(SAMPLE)
    after = nz.protected_lines(res.text)
    same = before == after
    digest_b = hashlib.sha256("".join(before).encode()).hexdigest()
    digest_a = hashlib.sha256("".join(after).encode()).hexdigest()
    w(f"- 受保护行数：{len(before)}（原文） vs {len(after)}（结果）\n")
    w(f"- 逐行相等：{'是' if same else '否'}\n")
    w(f"- 原文保护区 SHA-256：`{digest_b}`\n")
    w(f"- 结果保护区 SHA-256：`{digest_a}`\n")
    w(f"- 哈希一致：{'是' if digest_b == digest_a else '否'}\n\n")
    w("受保护行原始内容（含缩进、制表符、行尾空白）：\n\n")
    for line in before:
        w(f"- {_vis(line)}\n")
    w("\n")

    w("## 4. 不变量检查\n\n")
    once = res.text
    twice = nz.normalize(once).text
    w(f"- 幂等性（规范化两次 == 规范化一次）：{'通过' if once == twice else '失败'}\n")
    w(f"- 行数不变：{'通过' if SAMPLE.count(chr(10)) == once.count(chr(10)) else '失败'}\n")
    w(f"- 引用块 '>' 数量不变："
      f"{'通过' if SAMPLE.count('>') == once.count('>') else '失败'}\n\n")

    w("## 5. 性能（10 万段落）\n\n")
    stats = bench.run(100_000)
    w(f"- 环境：Python {platform.python_version()} / {platform.platform()}\n")
    w(f"- 段落数：{stats['paragraphs']:,}（{stats['lines']:,} 行，"
      f"输入 {stats['input_bytes'] / 1024 / 1024:.1f} MiB）\n")
    w(f"- 耗时：{stats['seconds']:.2f} 秒"
      f"（{stats['paragraphs_per_sec']:,.0f} 段落/秒）\n")
    w(f"- 差异条数：{stats['changes']:,}\n")
    w(f"- 内存峰值（tracemalloc，规范化期间 Python 堆分配）："
      f"{stats['tracemalloc_peak_mb']:.1f} MiB\n")
    w(f"- 进程峰值 RSS（ru_maxrss，含解释器与输入数据）："
      f"{stats['ru_maxrss_mb']:.1f} MiB\n")
    return out.getvalue()


def main() -> None:
    report = build()
    with open("REPORT.md", "w", encoding="utf-8") as f:
        f.write(report)
    sys.stdout.write(report)


if __name__ == "__main__":
    main()
