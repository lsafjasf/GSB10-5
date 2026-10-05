#!/usr/bin/env bash
# 一键运行：自测 -> 四场景校验 -> 1000 并发基准，数据写入 reports/
set -euo pipefail
cd "$(dirname "$0")"

echo "== 1/3 单元自测 =="
python3 -m unittest discover -s tests -v

echo "== 2/3 场景校验（单用例/并行100/残留/回收失败） =="
python3 tests/run_scenarios.py

echo "== 3/3 1000 并发基准 =="
python3 bench/bench_1000.py

echo "全部完成，报告见 reports/"
