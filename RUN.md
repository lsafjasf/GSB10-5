# 运行命令

所有命令在仓库根目录执行，仅依赖 Python 3 标准库（开发环境 3.12.3）。

```bash
# 1. 自测（词法/优先级/结合性/语义/错误分类/恶意拒绝，27 项）
python3 -m unittest tests.test_core -v

# 2. 对拍（固定 20 条 + 随机 1000 条 x 3 行，输出 data/differential_cases.jsonl）
python3 tools/differential.py                      # 默认 1000 条随机
python3 tools/differential.py --count 5000         # 可加大规模

# 3. 生成恶意表达式拒绝清单（输出 REJECTIONS.md）
python3 tools/rejections.py

# 4. 基准测试（默认 100 万行 x 1000 条表达式）
python3 tools/benchmark.py --json data/benchmark_full.json
python3 tools/benchmark.py --rows 10000 --exprs 1000   # 快速试跑
```

交付物索引：

| 交付物 | 位置 |
|--------|------|
| 源码 | `filterexpr/` |
| 自测 | `tests/test_core.py` |
| 语法说明 | `GRAMMAR.md` |
| 对拍数据 | `data/differential_cases.jsonl`（工具：`tools/differential.py`） |
| 拒绝清单 | `REJECTIONS.md`（工具：`tools/rejections.py`） |
| 耗时数据 | `BENCHMARK.md`（工具：`tools/benchmark.py`） |
