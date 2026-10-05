"""textree 自测: 宽度断言、往返对拍、超宽词强制断开、10 万节点性能。

运行: python3 test_textree.py
仅依赖标准库。
"""

import gc
import random
import sys
import time
import tracemalloc

from textree import (
    char_width,
    display_width,
    render,
    parse,
    wrap_text,
    escape_text,
    unescape_text,
)

PASS = "[PASS]"
FAIL = "[FAIL]"
passed = 0
failed = 0


def check(cond, msg):
    global passed, failed
    if cond:
        passed += 1
        print("%s %s" % (PASS, msg))
    else:
        failed += 1
        print("%s %s" % (FAIL, msg))


def rendered_lines(text):
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def assert_width_bounds(text, width, label):
    """断言: 每一物理行的显示宽度(含缩进/续行标记)都不超过 width。"""
    lines = rendered_lines(text)
    widths = [display_width(line) for line in lines]
    worst = max(widths) if widths else 0
    over = [w for w in widths if w > width]
    check(not over,
          "%s: %d 物理行, 最大显示宽度 %d <= 上限 %d (逐行断言)"
          % (label, len(lines), worst, width))
    return worst


def assert_roundtrip(node, width, label):
    text = render(node, width)
    back = parse(text)
    check(back == node, "%s: 往返解析后结构与内容完全一致" % label)
    return text


# ---------------------------------------------------------------- 情形 1
def test_single_level():
    node = {
        "text": "root",
        "children": [
            {"text": "alpha: 第一个子节点 short", "children": []},
            {"text": "beta: this is a plain ascii sentence that wraps on spaces",
             "children": []},
            {"text": "gamma", "children": []},
        ],
    }
    width = 30
    text = assert_roundtrip(node, width, "情形1 单层结构")
    assert_width_bounds(text, width, "情形1 单层结构")
    print("---- 单层结构渲染样例 (width=30) ----")
    print(text, end="")
    print("-------------------------------------")


# ---------------------------------------------------------------- 情形 2
def test_deep_nesting():
    depth = 30
    node = {"text": "level 0", "children": []}
    cur = node
    for d in range(1, depth + 1):
        child = {"text": "level %d 嵌套节点" % d, "children": []}
        cur["children"].append(child)
        cur = child
    width = 2 * depth + 10  # 保证 width >= 2*D+4
    text = assert_roundtrip(node, width, "情形2 深嵌套(深度30)")
    assert_width_bounds(text, width, "情形2 深嵌套(深度30)")
    # 最深一行缩进宽度必须计入检查: 缩进 60 格
    deepest = rendered_lines(text)[-1]
    check(len(deepest) - len(deepest.lstrip(" ")) == 60,
          "情形2: 最深行缩进为 60 个空格(缩进计入宽度)")


# ---------------------------------------------------------------- 情形 3
def test_wide_chars():
    node = {
        "text": "根节点: 中文与 English 混排 CJK 全角字符",
        "children": [
            {"text": "子节点一: 这是一段包含大量中文的文本需要在空格处折行显示",
             "children": []},
            {"text": "子节点二: ＡＢＣ全角字母 abc 半角混合 你好世界 hello",
             "children": []},
        ],
    }
    width = 24
    text = assert_roundtrip(node, width, "情形3 含超宽字符(CJK/全角)")
    assert_width_bounds(text, width, "情形3 含超宽字符(CJK/全角)")
    print("---- 超宽字符渲染样例 (width=24) ----")
    print(text, end="")
    print("--------------------------------------")
    check(char_width("中") == 2 and char_width("Ａ") == 2
          and char_width("a") == 1 and char_width("é".replace("e", "e")) == 1
          and char_width("\u0301") == 0,
          "情形3: 单字符显示宽度 CJK/全角=2, ASCII=1, 组合符=0")


# ---------------------------------------------------------------- 情形 4
def test_long_word():
    long_word = "x" * 150
    node = {"text": "word:" + long_word + ":end", "children": []}
    width = 20
    text = render(node, width)
    assert_width_bounds(text, width, "情形4 含超长不可断词")
    back = parse(text)
    check(back == node, "情形4: 强制断开后往返解析一致(无字符增删)")
    lines = rendered_lines(text)
    # 首行可用 20 格 -> "word:" 占 5 格, 然后 15 个 x; 之后每段 20 个 x
    # 续行可用 18 格(缩进0 + "| " 2格) -> 18 个 x
    body = [ln[2:] if ln.startswith("| ") else ln for ln in lines]
    check(body[0] == "word:" + "x" * 15,
          "情形4: 首行 = 'word:' + 15个x (显示宽度 20)")
    check(all(len(seg) == 18 for seg in body[1:-1]),
          "情形4: 中间续行每段恰好 18 个 x (20-2 续行标记)")
    joined = "".join(body)
    check(joined == "word:" + long_word + ":end",
          "情形4: 分段拼接严格等于原文(强制断开规则确定)")
    print("---- 超长词强制断开样例 (width=20, 仅显示前 6 物理行) ----")
    for ln in lines[:6]:
        print(repr(ln), "-> 显示宽度", display_width(ln))
    print("... 共 %d 物理行 ..." % len(lines))
    print("------------------------------------------------")


# ---------------------------------------------------------------- 边界文本
def test_nasty_texts():
    nasty = [
        "",
        " ",
        "   多个前导空格",
        "| 开头是续行标记",
        "|",
        "back\\slash and \\n \\r \\t 转义",
        "含真实换行\n第二段落\t带制表符\r回车",
        "a" * 100,
        "中" * 40,
        "空格结尾   ",
        "  ",
        "|foo 不该被转义",
        "组合字符: e\u0301 a\u0302",
    ]
    ok_all = True
    for i, t in enumerate(nasty):
        node = {"text": t, "children": [{"text": "child of %d" % i,
                                         "children": []}]}
        text = render(node, 24)
        try:
            back = parse(text)
            same = back == node
        except ValueError:
            same = False
        widths = [display_width(ln) for ln in rendered_lines(text)]
        same = same and all(w <= 24 for w in widths)
        if not same:
            ok_all = False
        print("  边界文本 %2d/%d: %-20r %s"
              % (i + 1, len(nasty), t[:18], "OK" if same else "FAIL"))
    check(ok_all, "情形5 边界文本: 全部往返一致且不超宽(逐条见上)")


# ---------------------------------------------------------------- 模糊对拍
def random_text(rng):
    pool = ["a", "b", "词", "Ａ", " ", " ", " ", "x", "中", "| ",
            "\\", "\n", "é", "\u0301", "1"]
    length = rng.randint(0, 60)
    return "".join(rng.choice(pool) for _ in range(length))


def random_tree(rng, depth, max_depth):
    node = {"text": random_text(rng), "children": []}
    if depth < max_depth:
        for _ in range(rng.randint(0, 3)):
            node["children"].append(random_tree(rng, depth + 1, max_depth))
    return node


def test_fuzz():
    rng = random.Random(20261005)
    trials = 500
    all_ok = True
    total_lines = 0
    worst_overall = 0
    for _ in range(trials):
        max_depth = rng.randint(0, 8)
        width = rng.randint(2 * max_depth + 4, 80)
        node = random_tree(rng, 0, max_depth)
        text = render(node, width)
        back = parse(text)
        if back != node:
            all_ok = False
            break
        for ln in rendered_lines(text):
            w = display_width(ln)
            total_lines += 1
            worst_overall = max(worst_overall, w)
            if w > width:
                all_ok = False
                break
        if not all_ok:
            break
    check(all_ok,
          "情形6 模糊对拍: %d 棵随机树全部往返一致且逐行不超宽 "
          "(累计 %d 物理行, 观察到最大宽度 %d)"
          % (trials, total_lines, worst_overall))


# 确定对拍数据: 具体的结构 <-> 文本样例
def test_fixed_pairs():
    node = {"text": "root 根",
            "children": [
                {"text": "a b", "children": []},
                {"text": "cccccccccc", "children": []},
            ]}
    expected = (
        "root 根\n"
        "  a b\n"
        "  cccccccccc\n"
    )
    got = render(node, 20)
    check(got == expected, "情形7 固定对拍: 渲染文本与预期逐字节一致")
    check(parse(expected) == node, "情形7 固定对拍: 预期文本解析回原结构")
    node2 = {"text": "short words here", "children": []}
    got2 = render(node2, 12)
    expected2 = "short words \n| here\n"
    check(got2 == expected2, "情形7 固定对拍: 空格折行 'short words here' -> 两行")
    check(parse(expected2) == node2, "情形7 固定对拍: 折行文本解析回原句")


# ---------------------------------------------------------------- 性能
def build_large_tree(total=100000, seed=42):
    rng = random.Random(seed)
    words = ["node", "数据", "alpha", "节点内容片段", "wrap",
             "measurement", "文本", "tree", "abcdefgh"]
    roots = 200

    def make_text():
        n = rng.randint(3, 12)
        parts = [rng.choice(words) for _ in range(n)]
        if rng.random() < 0.05:
            parts.append("z" * rng.randint(30, 80))
        return " ".join(parts)

    top = {"text": "root", "children": []}
    made = 1
    branches = []
    for _ in range(roots):
        branch = {"text": make_text(), "children": []}
        top["children"].append(branch)
        branches.append(branch)
        made += 1
    i = 0
    while made < total:
        branches[i % roots]["children"].append(
            {"text": make_text(), "children": []})
        made += 1
        i += 1
    return top, made


def count_nodes(node):
    return 1 + sum(count_nodes(c) for c in node["children"])


def test_performance():
    width = 80
    node, made = build_large_tree()
    n_nodes = count_nodes(node)
    check(n_nodes == 100000 and made == 100000,
          "情形8 性能: 构造恰好 100000 个节点")

    # 时间测量(不开 tracemalloc, 避免拖慢计时)
    gc.collect()
    t0 = time.perf_counter()
    text = render(node, width)
    t1 = time.perf_counter()
    render_ms = (t1 - t0) * 1000

    t0 = time.perf_counter()
    back = parse(text)
    t1 = time.perf_counter()
    parse_ms = (t1 - t0) * 1000

    # 内存峰值(tracemalloc 只统计 Python 分配; 单独跑一次)
    gc.collect()
    tracemalloc.start()
    text2 = render(node, width)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    lines = rendered_lines(text)
    over = [ln for ln in lines if display_width(ln) > width]
    check(not over and back == node and len(text2) == len(text),
          "情形8 性能: 10 万节点渲染结果逐行 <= %d 且往返一致 (%d 物理行, %d 输出字节)"
          % (width, len(lines), len(text.encode("utf-8"))))
    print("---- 10 万节点性能数据 (width=%d) ----" % width)
    print("节点数:          %d" % n_nodes)
    print("物理行数:        %d" % len(lines))
    print("输出大小:        %d 字节 (UTF-8)" % len(text.encode("utf-8")))
    print("渲染耗时:        %.1f ms" % render_ms)
    print("解析耗时:        %.1f ms" % parse_ms)
    print("渲染内存峰值:    %.1f MiB (tracemalloc)" % (peak / 1024 / 1024))
    print("----------------------------------------")


def main():
    test_single_level()
    test_deep_nesting()
    test_wide_chars()
    test_long_word()
    test_nasty_texts()
    test_fuzz()
    test_fixed_pairs()
    test_performance()
    print()
    print("总计: %d 通过, %d 失败" % (passed, failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
