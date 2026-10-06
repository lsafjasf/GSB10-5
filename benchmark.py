#!/usr/bin/env python3
"""10 万错误码压测：生成 -> 校验 -> 翻译，统计耗时与内存峰值。

用法：python3 benchmark.py [--count 100000]
"""

import argparse
import json
import os
import sys
import time
import tracemalloc

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from errcodes import build, check_stability, make_code, Translator
from errcodes.core import AREA_MAP, CATEGORY_NAMES


def generate(count):
    """生成 count 个合法定义：循环使用已登记业务域，类别/序号递增。"""
    modules = sorted(AREA_MAP.items())  # [(module, area), ...]
    n_modules = len(modules)
    loaded = []
    for i in range(count):
        module, area = modules[i % n_modules]
        per_module_index = i // n_modules  # 模块内序号，保证码唯一
        category = per_module_index // 899 % 99 + 1
        serial = per_module_index % 899 + 1
        loaded.append((module, {
            "code": make_code(area, category, serial),
            "name": "ERR_%08d" % i,
            "default_message": "error %d occurred" % i,
        }))
    return loaded


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=100000)
    args = parser.parse_args()
    count = args.count

    print("生成 %d 个错误定义..." % count)
    t0 = time.perf_counter()
    loaded = generate(count)
    t_gen = time.perf_counter() - t0

    tracemalloc.start()
    t0 = time.perf_counter()
    registry = build(loaded)
    t_validate = time.perf_counter() - t0

    messages = {code: "翻译 %s" % code
                for i, (code, _) in enumerate(registry.entries.items())
                if i % 10 != 0}  # 故意留 10% 缺失，验证覆盖率统计在大数据量下正确
    translator = Translator(registry, "zh-CN", messages)

    t0 = time.perf_counter()
    coverage = translator.coverage()
    t_coverage = time.perf_counter() - t0

    t0 = time.perf_counter()
    for code in registry.entries:
        translator.translate(code)
    t_translate_all = time.perf_counter() - t0

    snapshot = {"version": "bench-baseline", "codes": {
        code: {"name": e["def"]["name"],
               "default_message": e["def"]["default_message"]}
        for code, e in registry.entries.items()}}
    t0 = time.perf_counter()
    stability = check_stability(registry, snapshot)
    t_stability = time.perf_counter() - t0

    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    result = {
        "count": count,
        "python": sys.version.split()[0],
        "generation_ms": round(t_gen * 1000, 1),
        "validation_ms": round(t_validate * 1000, 1),
        "coverage_ms": round(t_coverage * 1000, 1),
        "translate_all_ms": round(t_translate_all * 1000, 1),
        "stability_ms": round(t_stability * 1000, 1),
        "memory_peak_mb": round(peak / 1024 / 1024, 1),
        "issues": len(registry.issues),
        "coverage_percent": coverage["coverage_percent"],
        "stability_breaking": stability["has_breaking_changes"],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))

    report_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
    os.makedirs(report_dir, exist_ok=True)
    with open(os.path.join(report_dir, "benchmark.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
