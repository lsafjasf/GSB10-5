"""定位精度校验 + 与纯文本匹配的对比实验。

1) 锚点校验：对每条报警，取报警行的物理文本，检查是否包含该规则对应的
   语法锚点（如 CR002 行必须以 except 开头），统计通过率。
2) 对比实验：用正则做“纯文本匹配版”检查器，在同一语料上统计它的
   误报（字符串/注释里的假命中）与漏报，说明为什么必须基于 AST。

用法: python3 tools/validate_locations.py [corpus 目录]
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from codereview import check_paths

CORPUS = os.path.join(os.path.dirname(__file__), "..", "tests", "corpus")


def anchor_ok(rule_id, text):
    stripped = text.strip()
    if rule_id == "CR001":
        return ("=" in text) and any(
            tok in text for tok in ("[]", "{}", "set()", "list(", "dict(", "set(")
        )
    if rule_id == "CR002":
        return stripped.startswith("except") and stripped.endswith(":")
    if rule_id == "CR003":
        return any(tok in text for tok in ("eval(", "exec(", "os.system("))
    if rule_id == "CR004":
        return "import" in text
    if rule_id == "CR005":
        return "execute" in text
    if rule_id == "CR006":
        return "import" in text
    return True


def validate(findings):
    per_rule = {}
    for finding in findings:
        with open(finding.path, encoding="utf-8") as fh:
            text = fh.readlines()[finding.line - 1]
        ok = anchor_ok(finding.rule_id, text)
        stats = per_rule.setdefault(finding.rule_id, [0, 0])
        stats[0] += 1
        stats[1] += ok
    print("== 锚点校验（报警行物理文本包含对应语法锚点）==")
    total, passed = 0, 0
    for rule_id in sorted(per_rule):
        n, ok = per_rule[rule_id]
        total += n
        passed += ok
        print(f"  {rule_id}: {ok}/{n} 通过")
    print(f"  合计: {passed}/{total} = {passed / total * 100:.2f}%")


NAIVE_RULES = {
    "CR001": re.compile(r"def\s+\w+\(.*=\s*(\[\]|\{\}|set\(\))"),
    "CR002": re.compile(r"^\s*except\s*:"),
    "CR003": re.compile(r"\b(eval|exec)\s*\("),
    "CR005": re.compile(r"\.execute\(\s*f?\""),
}


def naive_scan(corpus):
    hits = []
    for dirpath, _dirnames, filenames in os.walk(corpus):
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8") as fh:
                for lineno, line in enumerate(fh, 1):
                    for rule_id, pattern in NAIVE_RULES.items():
                        if pattern.search(line):
                            hits.append((rule_id, os.path.relpath(path, corpus), lineno))
    return hits


def compare_with_naive(corpus):
    with open(os.path.join(corpus, "expected.json"), encoding="utf-8") as fh:
        expected = {(e["rule_id"], e["file"], e["line"]) for e in json.load(fh)}
    naive = set(naive_scan(corpus))
    naive = {(r, f, l) for r, f, l in naive if r in {e[0] for e in expected}}
    ast_hits = {
        (f.rule_id, os.path.relpath(f.path, corpus), f.line)
        for f in check_paths([corpus])
    }
    print("== 纯文本匹配（正则）在同一语料上的表现 ==")
    for rule_id in sorted(NAIVE_RULES):
        exp = {e for e in expected if e[0] == rule_id}
        got = {h for h in naive if h[0] == rule_id}
        fp = got - exp
        fn = exp - got
        ast_got = {a for a in ast_hits if a[0] == rule_id}
        print(
            f"  {rule_id}: 文本匹配 误报{len(fp)} 漏报{len(fn)} | "
            f"AST 误报{len(ast_got - exp)} 漏报{len(exp - ast_got)}"
        )
        for rule, path, line in sorted(fp):
            print(f"    文本误报示例: {path}:{line}")


def main():
    corpus = sys.argv[1] if len(sys.argv) > 1 else CORPUS
    findings = check_paths([corpus])
    validate(findings)
    compare_with_naive(corpus)


if __name__ == "__main__":
    main()
