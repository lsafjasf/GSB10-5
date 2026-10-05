"""会话标识随机性来源说明与生成样本统计。

随机性来源：secrets.token_hex(16) -> os.urandom -> Linux getrandom(2)，
即操作系统级 CSPRNG，128 bit 熵，不可预测（非 random 模块的 PRNG）。

运行：python3 randomness_check.py
"""

import math
import secrets
import sys
from collections import Counter

N = 200_000
ID_BYTES = 16

print(f"随机性来源链: secrets.token_hex({ID_BYTES}) -> os.urandom -> getrandom(2) (内核 CSPRNG)")
print(f"标识空间: 2^{ID_BYTES * 8} = 2^128，暴力碰撞期望需 ~2^64 次生成（生日界）")
print(f"生成样本数: {N:,}")

samples = [secrets.token_hex(ID_BYTES) for _ in range(N)]

# 1) 唯一性
uniq = len(set(samples))
print(f"[唯一性] {N:,} 个样本中不同标识 {uniq:,} 个，碰撞 {N - uniq} 次")

# 2) 长度与字符集
assert all(len(s) == 32 for s in samples)
assert all(c in "0123456789abcdef" for s in samples for c in s[:64])
print("[格式] 全部样本均为 32 位小写十六进制（128 bit）")

# 3) 字节分布卡方检验（均匀性）
byte_counter = Counter()
for s in samples[:50_000]:
    byte_counter.update(bytes.fromhex(s))
total = sum(byte_counter.values())
expected = total / 256
chi2 = sum((byte_counter.get(b, 0) - expected) ** 2 / expected for b in range(256))
df = 255
# 正态近似：均值 df，标准差 sqrt(2*df)；|z| < 4 视为通过
z = (chi2 - df) / math.sqrt(2 * df)
print(f"[均匀性] 字节分布卡方 chi2={chi2:.1f} (df=255, 正态近似 z={z:+.2f}, |z|<4 通过)")
print(f"         最少见字节出现 {min(byte_counter.values()):,} 次，"
      f"最多见 {max(byte_counter.values()):,} 次，期望 {expected:,.0f} 次")

# 4) 相邻样本汉明距离（无相关性迹象）
def hamming_hex(a: str, b: str) -> int:
    return sum(bin(int(x, 16) ^ int(y, 16)).count("1") for x, y in zip(a, b))

dists = [hamming_hex(samples[i], samples[i + 1]) for i in range(10_000)]
avg = sum(dists) / len(dists)
print(f"[独立性] 相邻样本平均汉明距离 {avg:.1f} / 128 bit（理论期望 64）")

print("\n样本前 5 个:")
for s in samples[:5]:
    print(f"  {s}")

if uniq != N or abs(z) >= 4:
    sys.exit("随机性自检未通过")
print("\n随机性自检通过 ✔")
