"""逐条判定明细演示：对典型样本打印每条策略的 PASS/FAIL 与原因。

运行: python3 demo.py
"""

from password_policy import DEFAULT_COMMON_PASSWORDS, PasswordPolicy

POLICY = PasswordPolicy(
    min_length=8,
    max_length=16,
    require_lower=True,
    require_upper=True,
    require_digit=True,
    require_symbol=True,
    no_whitespace=True,
    max_repeat=3,
    common_passwords=DEFAULT_COMMON_PASSWORDS,
    username="alice",
)

SAMPLES = [
    ("全部通过", "Str0ng!pw"),
    ("只违反一条（缺数字）", "Strong!pw"),
    ("违反多条", "alice 1"),
    ("刚好等于最小长度(8)", "Aa1!bcde"),
    ("刚好小于最小长度(7)", "Aa1!bcd"),
    ("刚好等于最大长度(16)", "Aa1!bcdefghijklm"),
    ("刚好超出最大长度(17)", "Aa1!bcdefghijklmn"),
    ("多字节字符(中文)", "口令口令Aa1!"),
    ("组合序列 e+U+0301", "e\u0301ample1!A"),
]


def main():
    for label, pw in SAMPLES:
        res = POLICY.validate(pw)
        print(f"=== {label}: {pw!r} ===")
        print(res.explain())
        print()


if __name__ == "__main__":
    main()
