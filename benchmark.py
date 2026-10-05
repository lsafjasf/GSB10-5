"""1000 条规则压测：输出耗时分布与内存峰值。

运行：python3 benchmark.py
"""

import json
import statistics
import sys

from inspector.limits import WALL_TIMEOUT_SEC
from inspector.runner import duration_distribution, run_rules

N = 1000

# 规则构成（固定种子、可复现）：
#   960 条正常规则（轻/中两种计算量）
#   10 条死循环
#   10 条超长正则回溯
#   10 条内存炸弹
#   10 条输出炸弹
NORMAL_LIGHT = (
    "import re\n"
    "def check(data):\n"
    "    return bool(re.search(r'\\d{3}-\\d{4}', data))"
)
NORMAL_MEDIUM = (
    "import json, math\n"
    "def check(data):\n"
    "    total = 0\n"
    "    for i in range(20000):\n"
    "        total += int(math.sqrt(i) * 10)\n"
    "    return total > 0"
)
DEATH_LOOP = "def check(data):\n    while True:\n        pass"
LONG_MATCH = (
    "import re\n"
    "def check(data):\n"
    "    return re.match(r'(a+)+$', 'a' * 30 + 'b')"
)
MEM_BOMB = (
    "def check(data):\n"
    "    x = []\n"
    "    while True:\n"
    "        x.append('A' * 10**6)"
)
OUTPUT_BOMB = (
    "def check(data):\n"
    "    for i in range(10**7):\n"
    "        print('x' * 1000)"
)


def build_rules():
    rules = []
    for i in range(960):
        src = NORMAL_LIGHT if i % 4 else NORMAL_MEDIUM
        rules.append((f"normal-{i:04d}", src))
    for i in range(10):
        rules.append((f"death-loop-{i:02d}", DEATH_LOOP))
    for i in range(10):
        rules.append((f"long-match-{i:02d}", LONG_MATCH))
    for i in range(10):
        rules.append((f"mem-bomb-{i:02d}", MEM_BOMB))
    for i in range(10):
        rules.append((f"output-bomb-{i:02d}", OUTPUT_BOMB))
    return rules


def main():
    rules = build_rules()
    assert len(rules) == N
    summary = run_rules(rules, data="订单号 010-8888 已创建")

    durations = [r.duration_ms for r in summary.results]
    dist = duration_distribution(summary.results)

    # 正常规则单独统计（排除异常规则的超时开销）
    normal = sorted(r.duration_ms for r in summary.results
                    if r.status == "ok")
    nd = normal

    def pct(vals, p):
        if not vals:
            return 0
        k = (len(vals) - 1) * p / 100
        lo = int(k)
        hi = min(lo + 1, len(vals) - 1)
        return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)

    normal_stats = {
        "count": len(normal),
        "min_ms": round(nd[0], 1),
        "p50_ms": round(pct(nd, 50), 1),
        "p90_ms": round(pct(nd, 90), 1),
        "p99_ms": round(pct(nd, 99), 1),
        "max_ms": round(nd[-1], 1),
        "mean_ms": round(statistics.mean(normal), 1),
    }

    report = {
        "规则总数": N,
        "并发度上限": 8,
        "墙钟超时_s": WALL_TIMEOUT_SEC,
        "总墙钟耗时_ms": round(summary.wall_ms, 1),
        "结果分类计数": summary.counts,
        "单条规则内存峰值_MiB": round(summary.peak_rss_kb / 1024, 1),
        "全部规则耗时分布_ms": dist,
        "仅正常规则耗时分布_ms": normal_stats,
        "串行等价总耗时_s": round(sum(durations) / 1000, 1),
        "实测并发加速比": round(sum(durations) / summary.wall_ms, 1),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    # 部分失败明细（每类取一条作为样例）
    print("\n部分失败结果样例：")
    seen = set()
    for r in summary.results:
        if r.status not in ("ok",) and r.status not in seen:
            seen.add(r.status)
            print(f"  {r.status:12s} {r.rule_id}  "
                  f"{round(r.duration_ms)}ms  {r.error}")
    return 0 if summary.counts.get("ok", 0) == 960 else 1


if __name__ == "__main__":
    sys.exit(main())
