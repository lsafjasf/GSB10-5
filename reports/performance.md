# 10 万行耗时与内存数据

## 测量方法

- 生成器 `tools/gen_benchmark.py` 用固定随机种子生成确定性项目到
  `build/bench/`：198 个 `.py` 文件，共 100,115 行；每个函数约
  9 行，包含循环、条件、f-string 等常见结构。
- 其中按周期植入 30 个真实问题 (RV001×19 / RV002×7 / RV006×4)，
  生成器写入 `expected.json`，检查后逐类核对数量，防止大规模漏报。
- 计时与内存分开两遍运行：`tracemalloc` 有 2–4 倍计时开销，耗时
  以无跟踪的一遍为准；内存峰值取 `tracemalloc` 跟踪到的 Python
  对象分配峰值，另附 `ru_maxrss` (整进程，含解释器)。

## 原始输出

```
files=198 lines=100115
elapsed=3.325s  (30,109 lines/s)
tracemalloc_peak=147.9 MiB
ru_maxrss=364.3 MiB (整进程, 含解释器)
findings=30 by_rule={'RV001': 19, 'RV002': 7, 'RV006': 4}
planted-check: OK
```

环境：Python 3.12.3 / Linux-6.18.33.1-microsoft-standard-WSL2-x86_64-with-glibc2.39 / 16 CPU

复现命令：

```bash
python3 tools/gen_benchmark.py
python3 tools/run_benchmark.py
```
