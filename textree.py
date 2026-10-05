"""textree -- 把嵌套结构渲染成等宽文本，并可无损解析回原结构。

仅使用 Python 标准库。

结构定义:
    node = {"text": str, "children": [node, ...]}

渲染格式:
    - 每个节点占一个逻辑行, 缩进 = 每深度 2 个空格。
    - 逻辑行按显示宽度折成若干物理行; 续行以 "| " 标记(与节点同级缩进)。
    - 所有物理行(含缩进与续行标记)的显示宽度都不超过 width。

显示宽度规则 (display_width):
    - East Asian Width 为 W/F 的字符(如 CJK 表意文字、全角符号)计 2 格;
    - 组合字符(Mn/Me)与控制/格式字符(Cc/Cf)计 0 格;
    - 其余字符计 1 格。

折行规则 (wrap_text), 贪心算法, 无损(所有分段拼接 == 原文):
    1. 优先在空格后断行, 空格保留在上一行行尾;
    2. 空格本身放不下时, 移到下一行行首;
    3. 单个词超过行宽时按显示单元硬断(强制断开), 不增删任何字符;
    4. 宽度为 2 的字符若放不进剩余 1 格, 整体移到下一行(该行尾留 1 空格位)。

转义规则 (escape_text / unescape_text), 保证可逆解析:
    - "\\" -> "\\\\", 换行 -> "\\n", 回车 -> "\\r", 制表符 -> "\\t";
    - 文本首字符为空格时转义为 "\\s"(避免与缩进混淆);
    - 文本以 "| " 开头或恰好为 "|" 时, 首字符转义为 "\\|"(避免与续行标记混淆)。

宽度保证:
    设最大深度为 D, 当 width >= 2*D + 4 时, 每个物理行(含缩进与续行
    标记)的显示宽度都不超过 width。深度过大使 indent >= width 时, 内容
    宽度退化为至少 1 格(此时宽度上限不再可保证, 属退化情形)。
"""

import unicodedata

__all__ = [
    "char_width",
    "display_width",
    "escape_text",
    "unescape_text",
    "wrap_text",
    "render",
    "parse",
]

INDENT_UNIT = "  "          # 每级深度的缩进
INDENT_WIDTH = 2            # 每级缩进的显示宽度
CONT_MARKER = "| "          # 续行标记
CONT_WIDTH = 2              # 续行标记的显示宽度


def char_width(ch):
    """单个字符的显示宽度: 宽字符 2 格, 组合/控制字符 0 格, 其余 1 格。"""
    if unicodedata.combining(ch):
        return 0
    if unicodedata.category(ch) in ("Cc", "Cf"):
        return 0
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2
    return 1


def display_width(s):
    """字符串的显示宽度(按终端显示单元计, 不是字符数)。"""
    return sum(char_width(c) for c in s)


def escape_text(text):
    """把节点文本转义成单行、不与行首语法冲突的形式。"""
    out = (
        text.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    if out.startswith(" "):
        out = "\\s" + out[1:]
    if out == "|" or out.startswith("| "):
        out = "\\" + out
    return out


def unescape_text(s):
    """escape_text 的逆操作。遇到非法转义序列抛 ValueError。"""
    out = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        if i + 1 >= n:
            raise ValueError("dangling backslash at end of text")
        nxt = s[i + 1]
        if nxt == "n":
            out.append("\n")
        elif nxt == "r":
            out.append("\r")
        elif nxt == "t":
            out.append("\t")
        elif nxt == "s":
            out.append(" ")
        elif nxt == "|":
            out.append("|")
        elif nxt == "\\":
            out.append("\\")
        else:
            raise ValueError("unknown escape sequence: \\%s" % nxt)
        i += 2
    return "".join(out)


def wrap_text(text, capacity):
    """按显示宽度把 text 贪心折行。

    capacity: 可调用对象, capacity(i) 返回第 i 个分段可用的显示格数
              (返回值会被钳制到至少 1)。
    返回分段列表。保证 "".join(分段) == text (无损)。
    """
    segs = []
    n = len(text)
    seg_start = 0
    seg_index = 0
    cur_w = 0
    last_space = -1  # 当前分段内最后一个空格的绝对下标
    i = 0
    while i < n:
        ch = text[i]
        w = char_width(ch)
        if cur_w + w <= max(1, capacity(seg_index)):
            cur_w += w
            if ch == " ":
                last_space = i
            i += 1
            continue
        if ch == " ":
            # 空格放不下: 移到下一分段行首
            segs.append(text[seg_start:i])
            seg_start = i
            seg_index += 1
            cur_w = 0
            last_space = -1
            continue
        if last_space >= seg_start:
            # 在当前分段最后一个空格后断行, 空格留在行尾
            segs.append(text[seg_start:last_space + 1])
            seg_start = last_space + 1
            seg_index += 1
            cur_w = display_width(text[seg_start:i])
            last_space = -1
            # 续行容量可能小于首行(续行标记占 2 格), 带入的词残余可能
            # 仍然超宽, 需要对残余反复硬断, 直到能容纳当前字符为止。
            while cur_w > max(1, capacity(seg_index)) and i > seg_start:
                pre_w = 0
                j = seg_start
                while j < i:
                    wj = char_width(text[j])
                    if pre_w + wj > max(1, capacity(seg_index)):
                        break
                    pre_w += wj
                    j += 1
                if j == seg_start:
                    j = seg_start + 1  # 退化: 宽字符独占一行
                segs.append(text[seg_start:j])
                seg_start = j
                seg_index += 1
                cur_w = display_width(text[seg_start:i])
            continue
        if i > seg_start:
            # 无空格可断: 硬断(强制断开超宽词)
            segs.append(text[seg_start:i])
            seg_start = i
            seg_index += 1
            cur_w = 0
            last_space = -1
            continue
        # 退化情形: 单个宽字符超过容量(容量为 1 时), 独占一行
        segs.append(ch)
        i += 1
        seg_start = i
        seg_index += 1
        cur_w = 0
        last_space = -1
    segs.append(text[seg_start:])
    return segs


def render(node, width=40):
    """把 node 渲染成文本(以换行结尾)。width 为每行显示宽度上限(含缩进)。"""
    if width < 1:
        raise ValueError("width must be >= 1")
    lines = []

    def rec(cur, depth):
        indent = INDENT_UNIT * depth
        indent_w = INDENT_WIDTH * depth

        def capacity(i, indent_w=indent_w):
            extra = 0 if i == 0 else CONT_WIDTH
            return max(1, width - indent_w - extra)

        segs = wrap_text(escape_text(cur["text"]), capacity)
        lines.append(indent + segs[0])
        for seg in segs[1:]:
            lines.append(indent + CONT_MARKER + seg)
        for child in cur["children"]:
            rec(child, depth + 1)

    rec(node, 0)
    return "\n".join(lines) + "\n"


def parse(text):
    """把 render 的输出解析回节点结构。格式非法时抛 ValueError。"""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # 去掉 render 输出末尾的换行; 空文本节点本身占一空行
    if not lines:
        raise ValueError("empty input")
    root = {"text": "", "children": []}  # 虚拟根, 深度 -1
    stack = [root]
    depths = [-1]
    prev_node = None
    prev_depth = None
    for lineno, raw in enumerate(lines, 1):
        stripped = raw.lstrip(" ")
        nspaces = len(raw) - len(stripped)
        if nspaces % 2 != 0:
            raise ValueError("line %d: odd indentation" % lineno)
        depth = nspaces // INDENT_WIDTH
        if stripped == "|" or stripped.startswith(CONT_MARKER):
            # 续行: 必须紧跟同深度的节点行或续行
            if prev_node is None or prev_depth != depth:
                raise ValueError("line %d: orphan continuation" % lineno)
            seg = "" if stripped == "|" else stripped[CONT_WIDTH:]
            prev_node["_raw"] += seg
            continue
        while depths[-1] >= depth:
            stack.pop()
            depths.pop()
        if depths[-1] != depth - 1:
            raise ValueError("line %d: bad nesting" % lineno)
        node = {"_raw": stripped, "children": []}
        stack[-1]["children"].append(node)
        stack.append(node)
        depths.append(depth)
        prev_node = node
        prev_depth = depth
    if len(root["children"]) != 1:
        raise ValueError("expected exactly one root node, got %d"
                         % len(root["children"]))

    def finalize(cur):
        cur["text"] = unescape_text(cur.pop("_raw"))
        for child in cur["children"]:
            finalize(child)

    finalize(root["children"][0])
    return root["children"][0]
