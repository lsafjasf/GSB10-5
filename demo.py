# -*- coding: utf-8 -*-
"""场景演示: 输出识别结果表 / 归并规则结果表 / 基数变化数据 / 反例验证。

运行: python3 demo.py
"""
import os
import random
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from cardinality_guard import (  # noqa: E402
    Config, CardinalityGuard,
    TRUNCATE, NORMALIZE_PATTERN, NORMALIZE_BUCKET, DROP,
)

METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH"]
STATUSES = ["200", "404", "500"]
REGIONS = ["cn-north", "cn-south", "us-east", "eu-west"]
ENDPOINTS = ["/api/v1/users", "/api/v1/orders", "/api/v1/items",
             "/api/v1/cart", "/api/v1/pay", "/health"]


def hr(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def report_table(reports):
    print(f"{'label':<12}{'distinct':>10}{'distinct/series':>17}"
          f"{'new_rate':>10}{'top_share':>11}  {'verdict':<38}{'rule'}")
    print("-" * 108)
    for label, r in sorted(reports.items()):
        verdict = ",".join(r.flags) if r.flags else "OK"
        print(f"{label:<12}{r.distinct:>10}{r.distinct_ratio:>17.3f}"
              f"{r.new_rate:>10.3f}{r.top_value_share:>11.4f}"
              f"  {verdict:<38}{r.rule}")


def cardinality(series, guard=None):
    keys = set()
    for row in series:
        labels = guard.reduce(row) if guard else row
        keys.add(tuple(sorted(labels.items())))
    return len(keys)


# ---------------------------------------------------------------- 场景 1
hr("场景 1: 低基数标签 (method x status x region) —— 不应被归并")
rows = [{"method": METHODS[i % 5], "status": STATUSES[i % 3],
         "region": REGIONS[i % 4]} for i in range(20_000)]
g1 = CardinalityGuard(Config(window=1000))
report_table(g1.fit(rows))
b, a = cardinality(rows), cardinality(rows, g1)
print(f"\n基数: 归并前 {b} -> 归并后 {a}  (不变, 查询表达力零损失)")

# ---------------------------------------------------------------- 场景 2
hr("场景 2: 单个高基数标签 order_id (订单号当标签, 一单一序列)")
rows = [{"method": METHODS[i % 5], "status": STATUSES[i % 3],
         "order_id": str(900_000_000 + i)} for i in range(20_000)]
g2 = CardinalityGuard(Config(window=1000))
report_table(g2.fit(rows))
b, a = cardinality(rows), cardinality(rows, g2)
print(f"\n基数: 归并前 {b} -> 归并后 {a}  (压缩 {b/a:.0f}x)")
print("示例: {'method':'GET','status':'200','order_id':'900000001'} ->",
      g2.reduce(rows[0]))

# ---------------------------------------------------------------- 场景 3
hr("场景 3: 多标签组合爆炸 (endpoint x user_id x session_id)")
rng = random.Random(7)
rows = []
for i in range(20_000):
    rows.append({
        "endpoint": ENDPOINTS[i % len(ENDPOINTS)],
        "user_id": "u%07d" % (i % 5000),          # 5000 用户, 人均 4 会话
        "session_id": "sess-%08x-%04x" % (0xA0000000 + i, i & 0xFFFF),
    })
g3 = CardinalityGuard(Config(window=1000))
report_table(g3.fit(rows))
b, a = cardinality(rows), cardinality(rows, g3)
print(f"\n基数: 归并前 {b} -> 归并后 {a}  (压缩 {b/a:.0f}x)")
print("示例:", rows[0], "->", g3.reduce(rows[0]))

# ---------------------------------------------------------------- 场景 4
hr("场景 4: 取值持续增长 (max_values 调到极大, 只能靠增长速率识别)")
cfg = Config(max_values=10_000_000, window=500)
g4 = CardinalityGuard(cfg)
for i in range(3000):
    g4.observe({"region": REGIONS[i % 4], "trace_id": f"trace-{i:08d}"})
report_table(g4.finalize())
print("\ntrace_id 取值数远未超 max_values, 但 new_rate=1.0 -> UNBOUNDED_GROWTH,")
print("在基数爆炸之前提前识别; 对照 region 的 new_rate=0.0。")

# ---------------------------------------------------------------- 规则结果表
hr("归并规则结果表 (每种规则: 输入 -> 输出 -> 取值数变化)")
g = CardinalityGuard(Config(window=1000))
pods = []
alpha = "abcdefghijklmnopqrstuvwxyz"
for i in range(3000):
    # 末段保证含数字 -> 一定是随机 token 段, 归一化结果稳定
    suffix = "".join(rng.choice(alpha) for _ in range(4)) + str(rng.randrange(10))
    pods.append(f"web-{i:08x}f-{suffix}")
devices = ["".join(rng.choice("ghijklmnopqrstuv0123456789")
                   for _ in range(20)) for _ in range(5000)]
rows = []
for i in range(20_000):
    rows.append({
        "endpoint": ENDPOINTS[i % len(ENDPOINTS)],
        "pod": pods[i % len(pods)],                # -> NORMALIZE_PATTERN
        "user_id": "u%07d" % (i % 5000),           # -> TRUNCATE
        "device": devices[i % len(devices)],       # -> NORMALIZE_BUCKET
        "order_id": str(900_000_000 + i),          # -> DROP
    })
reports = g.fit(rows)
report_table(reports)

print(f"\n{'rule':<20}{'输入示例':<34}{'输出示例':<22}{'取值数: 前 -> 后'}")
print("-" * 100)
for label in ("pod", "user_id", "device", "order_id"):
    r = reports[label]
    raw = rows[0][label]
    reduced = g.reduce({label: raw}).get(label, "<标签已删除>")
    before = r.distinct
    after = len({g.reduce({label: row[label]}).get(label, "")
                 for row in rows}) if r.rule != DROP else 0
    print(f"{r.rule:<20}{raw:<34}{str(reduced):<22}{before:>6} -> {after}")

# ---------------------------------------------------------------- 反例验证
hr("反例验证: 归并不得合并本该区分的维度")
rows = []
for i in range(20_000):
    rows.append({"method": METHODS[i % 5], "status": STATUSES[i % 3],
                 "region": REGIONS[i % 4], "endpoint": ENDPOINTS[i % 6],
                 "order_id": str(900_000_000 + i)})
g5 = CardinalityGuard(Config(window=1000))
g5.fit(rows)

checks = []

# 反例 1: 数字状态码不能被模式归一化吞掉 (200/404/500 必须保持可区分)
statuses_after = {g5.reduce({"status": s, "order_id": "1"})["status"]
                  for s in STATUSES}
checks.append(("status 是纯数字, 但低基数 -> 不归一化, 200/404/500 仍可区分",
               statuses_after == set(STATUSES)))

# 反例 2: region 不能被截断合并
regions_after = {g5.reduce({"region": r})["region"] for r in REGIONS}
checks.append(("region 四个取值归并后仍两两不同", regions_after == set(REGIONS)))

# 反例 3: 仅 order_id 不同的序列合并(预期), method 不同的序列不得合并
ra = g5.reduce({"method": "GET", "status": "200", "order_id": "900000001"})
rb = g5.reduce({"method": "GET", "status": "200", "order_id": "900000002"})
rc = g5.reduce({"method": "POST", "status": "200", "order_id": "900000002"})
checks.append(("仅 order_id 不同的序列合并(预期行为)", ra == rb))
checks.append(("method 不同的序列不合并", ra != rc))

# 反例 4: 聚合正确性 —— 归并前后按 (method,status) 求和一致
before = Counter()
after = Counter()
for i, row in enumerate(rows):
    v = 1 + i % 7
    before[(row["method"], row["status"])] += v
    r = g5.reduce(row)
    after[(r["method"], r["status"])] += v
checks.append(("按 (method,status) 聚合结果归并前后完全一致", before == after))

# 反例 5: 5xx 告警表达力 —— reduce 后仍能筛出 status=500
n500 = sum(1 for row in rows if g5.reduce(row)["status"] == "500")
checks.append(("归并后仍可查询 status=\"500\" 的序列", n500 > 0))

width = max(len(c) for c, _ in checks)
for desc, ok in checks:
    print(f"  [{'PASS' if ok else 'FAIL'}] {desc}")
assert all(ok for _, ok in checks), "存在反例验证失败!"
print("\n全部反例验证通过。")
