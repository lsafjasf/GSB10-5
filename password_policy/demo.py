"""演示：对代表性口令输出逐条判定明细。

运行: python3 demo.py
"""
from password_policy import PasswordPolicy

CASES = [
    ("全部通过", "Str0ng!Pass"),
    ("只违反一条（太短）", "Aa1!xyz"),
    ("违反多条（短+缺大写+缺数字+缺符号）", "abc"),
    ("边界：恰好最小长度 8", "Aa1!wxyz"),
    ("边界：恰好最大长度 16", "Aa1!" + "wxyz" * 3),
    ("边界：超出最大长度 1", "Aa1!" + "wxyz" * 3 + "q"),
    ("多字节：含汉字，按字符数判定", "密码Ab1!xy"),
    ("组合序列：e + 组合重音符 = 2 字符", "é" + "Aa1!wxy"),
]

policy = PasswordPolicy(min_length=8, max_length=16)
for label, pw in CASES:
    print(f"=== {label}: {pw!r} ===")
    print(policy.validate(pw).explain())
    print()
