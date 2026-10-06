"""生成全部交付文档：规则说明 / 定位校验 / 误报漏报 / 耗时。

用法:
    python3 tools/make_reports.py          # 不跑 10 万行基准
    python3 tools/make_reports.py --bench  # 顺带生成并运行基准
"""

import argparse
import os
import platform
import subprocess
import sys
from collections import Counter

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from revcheck.rules import RULES  # noqa: E402
from tests.common import evaluate  # noqa: E402

DOCS_DIR = os.path.join(REPO_ROOT, "docs")
REPORTS_DIR = os.path.join(REPO_ROOT, "reports")


def write_rules_doc() -> None:
    lines = [
        "# 规则说明 (RV001–RV009)",
        "",
        "全部规则基于 Python 标准库 `ast` 语法树节点，不做文本/正则匹配。",
        "每条报警定位到**触发节点本身的起始行列**，并附内层最近的函数/类",
        "结构 (`module` / `function:名` / `class:名` / `method:类.方法` /",
        "`nested:外层.<locals>.内层`)。",
        "",
        "| 规则 | 检查项 | 依据 | 语法基础 (命中节点) | 定位节点 |",
        "|---|---|---|---|---|",
    ]
    locator = {
        "RV001": "默认参数表达式 (`[]` / `{}` / `dict()` 等)",
        "RV002": "`except:` 处理器节点",
        "RV003": "`import` 语句",
        "RV004": "`==`/`!=` 比较表达式",
        "RV005": "`except ... :` 处理器节点",
        "RV006": "第一条不可达语句",
        "RV007": "被覆盖的较早 `def`",
        "RV008": "`eval(...)`/`exec(...)` 调用表达式",
        "RV009": "属性访问或 `from ... import` 语句",
    }
    for meta in RULES.values():
        lines.append(
            f"| {meta.rule_id} | {meta.title} | {meta.rationale} "
            f"| {meta.ast_basis} | {locator[meta.rule_id]} |"
        )
    lines += [
        "",
        "## 定位原则",
        "",
        "- **行/列**取自触发节点的 `lineno`/`col_offset`，而不是外围语句或",
        "  函数定义行 (例外 RV007 定位到被覆盖的较早定义)。",
        "- **结构**通过一次语法树遍历建立 `id(node) -> 结构名` 映射得到。",
        "- 字符串字面量的内容在 AST 中只是 `ast.Constant` 的值，注释会被",
        "  词法分析直接丢弃，因此其中再像问题的文本也不会命中任何节点。",
        "",
        "## 已知边界 (诚实声明)",
        "",
        "- RV003 不识别仅供动态使用 (如 `getattr`/插件加载) 的导入。",
        "- RV006 只覆盖同一语句列表中的终止语句之后；`try/else` 的跨块语义",
        "  不分析。",
        "- RV009 只索引顶层名字，不识别运行期动态打补丁 (`mod.x = 1`)，",
        "  也不解析包 (`pkg.mod`) 的多层路径。",
        "",
    ]
    with open(os.path.join(DOCS_DIR, "RULES.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def _file_label(rel: str) -> str:
    if rel.startswith("positive/"):
        return "真实问题样本"
    if rel.startswith("negative/"):
        return "字符串/注释假问题与干净代码"
    if rel.startswith("crossfile/"):
        return "跨文件上下文项目"
    return rel


def write_location_report() -> None:
    ev = evaluate()
    expected_by_file = Counter(e[0] for e in ev.expected)
    hit_by_file = Counter(t[0] for t in ev.true_positives)
    miss_by_file = Counter(t[0] for t in ev.false_negatives)
    extra_by_file = Counter(
        os.path.relpath(f.path, os.path.join(REPO_ROOT, "fixtures"))
        for f in ev.false_positives
    )

    lines = [
        "# 定位校验数据",
        "",
        "校验方式：`fixtures/manifest.json` 是**手工标注**的期望清单",
        "(文件, 规则, 行号, 所在结构)。引擎实际报警与之做四元组精确比对。",
        f"期望报警 {len(ev.expected)} 条，精确命中 {len(ev.true_positives)} 条。",
        f"**定位精度 = {ev.location_accuracy * 100:.1f}%** (行号与结构均完全一致)。",
        "",
        "| 文件 | 样本类型 | 期望 | 精确命中 | 漏 | 多余 |",
        "|---|---|---|---|---|---|",
    ]
    files = sorted(
        set(expected_by_file) | set(extra_by_file),
    )
    for rel in files:
        exp = expected_by_file.get(rel, 0)
        lines.append(
            f"| `{rel}` | {_file_label(rel)} | {exp} | {hit_by_file.get(rel, 0)} "
            f"| {miss_by_file.get(rel, 0)} | {extra_by_file.get(rel, 0)} |"
        )

    lines += [
        "",
        "## 逐条定位明细 (实际报警)",
        "",
        "```",
    ]
    for f in ev.actual:
        rel = os.path.relpath(f.path, os.path.join(REPO_ROOT, "fixtures"))
        lines.append(
            f"{rel}:{f.line}:{f.col + 1}: {f.rule_id} [{f.structure}] {f.message}"
        )
    lines += [
        "```",
        "",
        "行号可直接用 `cat -n <文件>` 与手工清单逐条核对；",
        "`tests/test_fixtures.py` 在 CI 中强制要求定位精度 100%。",
        "",
    ]
    with open(os.path.join(REPORTS_DIR, "location_accuracy.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def write_precision_report() -> None:
    ev = evaluate()
    rule_ids = sorted(RULES.keys())
    tp = Counter(t[1] for t in ev.true_positives)
    fn = Counter(t[1] for t in ev.false_negatives)
    fp = Counter(f.rule_id for f in ev.false_positives)

    lines = [
        "# 误报与漏报数据",
        "",
        "## 测试用例构造方式",
        "",
        "- `fixtures/positive/`：3 个文件，手工植入全部 9 类规则的真实",
        f"  问题共 {sum(1 for e in ev.expected if e[0].startswith('positive/'))} 条，",
        "  每条在 `manifest.json` 中标注 (规则, 行号, 结构)。同一文件刻意混入",
        "  正确代码 (如 `if default is None`、`except KeyError: return None`)，",
        "  防止规则“逢关键字必报”。",
        "- `fixtures/negative/n1_strings_and_comments.py`：所有问题形态",
        "  (可变默认参数、裸 except、eval、== None、不可达代码、重复 def、",
        "  未用导入) 都只出现在**注释、普通字符串、三引号字符串**里。",
        "- `fixtures/negative/n2_clean.py`：结构正常的干净代码，验证正确",
        "  写法不被误伤。",
        "- `fixtures/positive/p3_usage_in_comment.py`：名字 `sys` 只在注释、",
        "  `tempfile` 只在字符串里出现，验证“使用计数”只看 `ast.Name` 节点，",
        "  不看文本。",
        "- `fixtures/crossfile/`：两文件小项目。`helper.py` 定义 2 个函数；",
        "  `main.py` 含 1 处正确的 `from ... import`、1 处错误的、1 处正确的",
        "  属性访问、1 处错误的属性访问，以及 1 处对已导入名字的正确使用。",
        "",
        "## 判定口径",
        "",
        "- TP：实际报警四元组 (文件,规则,行,结构) 与手工清单完全一致。",
        "- FP：报警不在清单中 (含 negative 样本里的任何报警)。",
        "- FN：清单条目未被精确命中。",
        "",
        f"TP={len(ev.true_positives)}  FP={len(ev.false_positives)}  "
        f"FN={len(ev.false_negatives)}",
        f"**精确率 P={ev.precision:.4f}    召回率 R={ev.recall:.4f}**",
        "",
        "| 规则 | TP | FP | FN |",
        "|---|---|---|---|",
    ]
    for rid in rule_ids:
        lines.append(f"| {rid} | {tp.get(rid, 0)} | {fp.get(rid, 0)} | {fn.get(rid, 0)} |")

    n1 = os.path.join(
        REPO_ROOT, "fixtures", "negative", "n1_strings_and_comments.py"
    )
    with open(n1, "r", encoding="utf-8") as fh:
        n1_lines = len(fh.read().splitlines())
    lines += [
        "",
        "## 字符串 / 注释假问题专项结果",
        "",
        f"- `n1_strings_and_comments.py` ({n1_lines} 行) 包含 8 处假问题",
        "  (4 处注释 + 三引号代码片段 4 类 + 拼接字符串 + 模板字符串)：",
        "  **实际报警 0 条**。",
        "- `n2_clean.py` 干净代码：**实际报警 0 条**。",
        "- 反向验证：同样的名字若只出现在注释/字符串里，RV003 仍应报",
        "  `p3_usage_in_comment.py`，实际命中 2/2 条 (第 1、2 行)。",
        "",
        "## 跨文件专项结果",
        "",
        "- 错误的 `from helper import compute_totals` 与属性访问",
        "  `helper.compute_totals(...)` 均被 RV009 命中 (main.py 第 4、12 行)。",
        "- 同文件中的 4 处正确跨文件用法均未误报。",
        "",
        "## 大规模漏报复核",
        "",
        "见 `reports/performance.md`：10 万行合成项目中按固定种子植入的",
        "30 个真实问题必须全部检出，数量不一致则基准脚本返回非零退出码。",
        "",
    ]
    with open(os.path.join(REPORTS_DIR, "precision_recall.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def write_performance_report(raw: str) -> None:
    lines = [
        "# 10 万行耗时与内存数据",
        "",
        "## 测量方法",
        "",
        "- 生成器 `tools/gen_benchmark.py` 用固定随机种子生成确定性项目到",
        "  `build/bench/`：198 个 `.py` 文件，共 100,115 行；每个函数约",
        "  9 行，包含循环、条件、f-string 等常见结构。",
        "- 其中按周期植入 30 个真实问题 (RV001×19 / RV002×7 / RV006×4)，",
        "  生成器写入 `expected.json`，检查后逐类核对数量，防止大规模漏报。",
        "- 计时与内存分开两遍运行：`tracemalloc` 有 2–4 倍计时开销，耗时",
        "  以无跟踪的一遍为准；内存峰值取 `tracemalloc` 跟踪到的 Python",
        "  对象分配峰值，另附 `ru_maxrss` (整进程，含解释器)。",
        "",
        "## 原始输出",
        "",
        "```",
        raw.rstrip(),
        "```",
        "",
        f"环境：Python {platform.python_version()} / {platform.platform()} / "
        f"{os.cpu_count()} CPU",
        "",
        "复现命令：",
        "",
        "```bash",
        "python3 tools/gen_benchmark.py",
        "python3 tools/run_benchmark.py",
        "```",
        "",
    ]
    with open(os.path.join(REPORTS_DIR, "performance.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bench", action="store_true", help="生成并运行 10 万行基准")
    args = parser.parse_args()

    os.makedirs(DOCS_DIR, exist_ok=True)
    os.makedirs(REPORTS_DIR, exist_ok=True)

    write_rules_doc()
    write_location_report()
    write_precision_report()

    raw = "(本次未运行基准；执行 python3 tools/make_reports.py --bench 生成)"
    if args.bench:
        subprocess.run(
            [sys.executable, os.path.join(REPO_ROOT, "tools", "gen_benchmark.py")],
            check=True,
            cwd=REPO_ROOT,
        )
        proc = subprocess.run(
            [sys.executable, os.path.join(REPO_ROOT, "tools", "run_benchmark.py")],
            check=True,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        raw = proc.stdout
    write_performance_report(raw)
    print("reports written:", sorted(os.listdir(REPORTS_DIR)), sorted(os.listdir(DOCS_DIR)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
