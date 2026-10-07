"""文本归一化。

归一化规则（详见 README「匹配规则」）：
1. NFKC 归一化：全角字符折叠为半角（ＡＰＩ -> API，１２３ -> 123），
   兼容字符折叠为标准形。依据：Unicode Standard Annex #15，NFKC 是
   面向「等价匹配」场景的标准做法。
2. 空白折叠：所有 Unicode 空白（含不换行空格 U+00A0、全角空格经 NFKC
   后也变为普通空格）折叠为单个 ASCII 空格。
3. 换行统一为空格：跨行书写不影响术语匹配。

为便于把命中位置映射回原文，normalize() 同时返回「归一化下标 -> 原文下标」
的映射表。报告中的行列号始终基于原文计算。
"""

import unicodedata

__all__ = ["normalize", "line_col"]


def normalize(text):
    """返回 (归一化文本, 下标映射表)。

    映射表 norm_to_orig[i] 表示归一化文本第 i 个字符对应原文中的下标。
    """
    out = []
    mapping = []
    prev_space = True  # 视为行首，吃掉开头空白
    for idx, ch in enumerate(text):
        decomposed = unicodedata.normalize("NFKC", ch)
        for dch in decomposed:
            if dch.isspace():
                if not prev_space:
                    out.append(" ")
                    mapping.append(idx)
                prev_space = True
            else:
                out.append(dch)
                mapping.append(idx)
                prev_space = False
    # 去掉末尾因折叠产生的空格
    if out and out[-1] == " ":
        out.pop()
        mapping.pop()
    return "".join(out), mapping


def line_col(text, offset):
    """把原文下标换算为 1 起始的 (行, 列)。"""
    line = text.count("\n", 0, offset) + 1
    last_nl = text.rfind("\n", 0, offset)
    col = offset - last_nl
    return line, col
