"""演示：区域划分、逐条差异、代码块逐字节校验、硬/软换行对照。

运行: python3 demo.py
"""

import difflib
import json

import mdnorm

SAMPLE = """# 标题\t 后有Tab和多余空格

这是第一行   软换行
这是第二行\t结尾硬换行  
这是第三行用反斜杠硬换行\\
这是第四行普通换行
再一行

> 引用第一行  结尾空白
> 引用第二行

普通段  落，后面紧跟围栏：

```python
def f( ):
    x = 1\t  # 缩进与Tab必须保留
    if x:
        return    "spaced"
```

缩进代码块：

    line one\twith tab
       line two  spaces

收尾段落   多空格
"""


def show_break_compare():
    print("=" * 70)
    print("硬换行 vs 软换行对照（⏎ 表示换行符）")
    print("=" * 70)
    text = "软换行第一行\n软换行第二行\n硬换行两空格  \n硬换行反斜杠\\\n末行\n"
    result, _ = mdnorm.normalize(text)
    print("原文字节视图  :", repr(text))
    print("结果字节视图  :", repr(result))
    print()
    for ln in result.rstrip("\n").split("\n"):
        tag = "硬换行(保留换行)" if ln.endswith(("  ", "\\")) else "段落行"
        print(f"  {ln!r:40} -> {tag}")
    print("  说明: 软换行两行已用单空格连接；硬换行处 \\n 原样保留。")


def show_regions_and_diffs():
    print()
    print("=" * 70)
    print("区域划分")
    print("=" * 70)
    regions = mdnorm.parse_regions(SAMPLE)
    for r in regions:
        first = r.raw.splitlines()[0][:48] if r.raw.strip() else "<空行>"
        print(f"  L{r.start:>2}-{r.end:<2} {r.kind:11} | {first}")

    result, diffs = mdnorm.normalize(SAMPLE)

    print()
    print("=" * 70)
    print(f"差异清单（共 {len(diffs)} 条）")
    print("=" * 70)
    for d in diffs:
        print(f"  [L{d.lineno}] {d.kind}")
        print(f"      原文: {d.original!r}")
        print(f"      结果: {d.normalized!r}")
        print(f"      依据: {d.explanation}")

    print()
    print("=" * 70)
    print("统一 diff 视图")
    print("=" * 70)
    ud = difflib.unified_diff(
        SAMPLE.splitlines(keepends=True),
        result.splitlines(keepends=True),
        fromfile="original.md", tofile="normalized.md")
    print("".join(ud))

    print("=" * 70)
    print("代码块逐字节校验")
    print("=" * 70)
    records = mdnorm.code_region_hashes(SAMPLE, result)
    print(f"  {'起始行':>4} {'类型':11} {'字节数':>6}  SHA-256(前16)  结果")
    for rec in records:
        print(f"  L{rec['start_line']:>3} {rec['kind']:11} {rec['bytes']:>6}  "
              f"{rec['sha256_original'][:16]}  "
              f"{'逐字节相同' if rec['identical'] else '不一致!!'}")
        assert rec["identical"]
    print()
    print(json.dumps(records, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    show_break_compare()
    show_regions_and_diffs()
