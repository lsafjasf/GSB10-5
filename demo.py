"""判定结果演示 + 乱序到达对拍。运行：python3 demo.py"""

import random

from windowing import WindowAssigner, WindowedCounter

SIZE, LATENESS, WATERMARK = 10, 15, 35
DATA = [0, 5, 10, 19, 20, 20, 29, 3, 100, 7, 9, 41, 30, 99, 1, 49]

assigner = WindowAssigner(size_ms=SIZE, allowed_lateness_ms=LATENESS)

print(f"窗口大小={SIZE} 允许迟到={LATENESS} 注入水位线={WATERMARK}")
print(f"规则: [start, end) 半开; end<=wm<end+{LATENESS} 为迟到并入; wm>=end+{LATENESS} 超迟到丢弃")
print()
print(f"{'event_time':>10} | {'window':>9} | status")
print("-" * 40)
for a in assigner.assign_batch(DATA, watermark=WATERMARK):
    print(f"{a.event_time:>10} | [{a.window_start:>3},{a.window_end:>3}) | {a.status}")

def run(order):
    c = WindowedCounter(assigner)
    c.add_batch(order, watermark=WATERMARK)
    return c.result()

rng = random.Random(42)
shuffled = DATA[:]
rng.shuffle(shuffled)
r1, r2 = run(DATA), run(shuffled)

print()
print(f"原始顺序: {DATA}")
print(f"乱序到达: {shuffled}")
print(f"顺序结果: {r1}")
print(f"乱序结果: {r2}")
print("对拍结论:", "一致 ✔" if r1 == r2 else "不一致 ✘")
