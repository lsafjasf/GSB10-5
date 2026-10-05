# 100 万条记录基准测试

- 环境：Python 3.12.3 / Linux-6.18.33.1-microsoft-standard-WSL2-x86_64-with-glibc2.39
- 记录数：1,000,000
- 准标识符（4 列）：age, gender, zip_code, occupation
- 评估组合数（1..3 阶）：14
- 数据生成耗时：4.88 s
- **检查耗时：7.41 s**（约 135,014 条/秒）
- **检查阶段 tracemalloc 峰值：143.3 MiB**
- 检查前进程 RSS：174.0 MiB
- **进程 RSS 峰值（含数据集）：510.7 MiB**
- 风险最高组合：`age + zip_code + occupation`，最小等价类 1，唯一组合 58,882 个，稀有记录 303,760 条，清单 303,760 行
