#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 run_selftest.py
python3 benchmark.py
echo "全部完成，报告见 reports/"
