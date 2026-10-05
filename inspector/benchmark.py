"""1000 条规则压测：python3 -m inspector.benchmark

规则构成：950 条正常规则（算术/字符串/正则匹配）+ 10 条死循环
+ 10 条灾难性正则（超长回溯）+ 15 条超大输出 + 15 条内存炸弹。
输出：逐规则耗时分布、内存峰值分布、状态汇总。
"""
import json
import resource

from inspector import Engine, Limits, Rule

LIMITS = Limits(wall_time_s=2.0, cpu_time_s=2, memory_mb=128,
                output_bytes=64 * 1024, result_bytes=4096)

NORMAL_VARIANTS = [
    "def check(facts):\n    return sum(range(facts['n'])) > 0\n",
    "def check(facts):\n    return all(k in facts for k in ('cpu', 'mem', 'disk'))\n",
    "def check(facts):\n    return re.match(r'^node-\\d+$', facts['host']) is not None\n",
    "def check(facts):\n    s = str(facts['n']) * 200\n    return len(s) == 200 * len(str(facts['n']))\n",
    "def check(facts):\n    return max(facts['samples']) < 1000\n",
]


def build_rules(n_total=1000):
    rules, i = [], 0
    counts = {"normal": 950, "loop": 10, "regex": 10, "output": 15, "memory": 15}
    facts = {"n": 1000, "host": "node-7", "cpu": 42, "mem": 55, "disk": 70,
             "samples": list(range(0, 1000, 2))}
    for _ in range(counts["normal"]):
        rules.append(Rule(f"normal-{i:04d}", NORMAL_VARIANTS[i % len(NORMAL_VARIANTS)],
                          facts, LIMITS)); i += 1
    for _ in range(counts["loop"]):
        rules.append(Rule(f"loop-{i:04d}",
                          "def check(facts):\n    while True:\n        pass\n",
                          None, LIMITS)); i += 1
    for _ in range(counts["regex"]):
        rules.append(Rule(f"regex-{i:04d}",
                          "def check(facts):\n"
                          "    return re.match(r'(a+)+$', 'a' * 28 + 'b')\n",
                          None, LIMITS)); i += 1
    for _ in range(counts["output"]):
        rules.append(Rule(f"output-{i:04d}",
                          "def check(facts):\n"
                          "    for j in range(10 ** 8):\n        print(j)\n",
                          None, LIMITS)); i += 1
    for _ in range(counts["memory"]):
        rules.append(Rule(f"memory-{i:04d}",
                          "def check(facts):\n    x = [0] * (10 ** 9)\n"
                          "    return bool(x)\n",
                          None, LIMITS)); i += 1
    assert len(rules) == n_total
    return rules


def percentile(sorted_vals, p):
    if not sorted_vals:
        return None
    idx = min(len(sorted_vals) - 1, int(round(p / 100 * (len(sorted_vals) - 1))))
    return sorted_vals[idx]


def main():
    rules = build_rules()
    engine = Engine(workers=8, limits=LIMITS)
    report = engine.run(rules)

    durations = sorted(r.duration_ms for r in report.results)
    rss_values = sorted(r.max_rss_kb for r in report.results if r.max_rss_kb)
    # RUSAGE_CHILDREN.ru_maxrss：所有已回收子进程的单点 RSS 峰值（最大者）
    children_peak_kb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    own_peak_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    result = {
        "rule_count": len(rules),
        "summary": report.summary,
        "duration_ms": {
            "min": round(durations[0], 2),
            "p50": round(percentile(durations, 50), 2),
            "p90": round(percentile(durations, 90), 2),
            "p95": round(percentile(durations, 95), 2),
            "p99": round(percentile(durations, 99), 2),
            "max": round(durations[-1], 2),
            "mean": round(sum(durations) / len(durations), 2),
        },
        "memory_peak": {
            "per_rule_rss_kb_min": rss_values[0],
            "per_rule_rss_kb_p99": percentile(rss_values, 99),
            "per_rule_rss_kb_max": rss_values[-1],
            "any_child_peak_kb": children_peak_kb,
            "parent_engine_peak_kb": own_peak_kb,
        },
        "limits": {
            "wall_time_s": LIMITS.wall_time_s, "cpu_time_s": LIMITS.cpu_time_s,
            "memory_mb": LIMITS.memory_mb, "output_bytes": LIMITS.output_bytes,
        },
        "workers": engine.workers,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return report


if __name__ == "__main__":
    main()
