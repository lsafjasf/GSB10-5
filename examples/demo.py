#!/usr/bin/env python3
"""对比两个文件形式的构建脚本并打印逐层判定。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from layercache.analysis import analyze_pair
from layercache.builder import CacheStore
from layercache.parser import parse_file

base = parse_file(os.path.join(os.path.dirname(__file__), "build_a.dockerfile"), "build_a")
cand = parse_file(os.path.join(os.path.dirname(__file__), "build_b.dockerfile"), "build_b")

report = analyze_pair(base, cand, CacheStore(), name="示例：中间层变化 + 仅元数据变化")

for verdict in report.verdicts:
    print(f"层 {verdict.index}: [{verdict.verdict:7s}] {verdict.instruction}")
print(f"\n层复用率: {report.layer_reuse_rate:.1%} "
      f"({report.reused_layers}/{report.total_layers})，"
      f"首个分叉层: {report.first_divergence_index}")
print(f"字节复用率: {report.byte_reuse_rate:.1%}，"
      f"复用正确性校验: {'通过' if report.verified else '失败'}")
