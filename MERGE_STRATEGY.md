# 子进程输出流合并（stream_merge）

把子进程的 stdout / stderr 合并成一个**可逆**的字节流：合并结果可以
字节级拆回原来的两个流。纯 Python 3 标准库实现。

## 文件清单

| 文件 | 说明 |
| --- | --- |
| `stream_merge.py` | 库：`merge()` / `run_merged()` / `merge_bytes()` / `split()` |
| `test_stream_merge.py` | 自测（12 个用例） |
| `bench_stream_merge.py` | 100 万行基准 |
| `gen_testdata.py` | 生成 `testdata/` 对拍数据 |
| `testdata/case*.{stdout,stderr,merged}` | 拆分对拍数据（6 组） |

## 合并策略对比与损失

| 策略 | 做法 | 损失 |
| --- | --- | --- |
| 按行 | 攒到换行才发一帧 | 无换行的尾数据要等 EOF 才可见（实时性差）；超长行会让缓冲随行长增长，内存无界 |
| 按块 | 读满固定大小就发一帧 | 一行可能被从中间撕开，跨帧拼行由消费方负责；实时性最好、内存严格有界 |
| 按时间 | 每帧带时间戳 | 时钟粒度有限，仍不能恢复真实交错时序；格式更重；同样要解决行内撕裂 |

**本项目采用「按行为主 + 超长行按块切」的混合策略**：

- 前 `max_record`（默认 64 KiB）字节内有换行 → 整行（含 `\n`）作为一帧；
- 没有换行且缓冲已达 `max_record` → 切出 `max_record` 一块作为一帧（超长行被拆成多帧）；
- 流结束（EOF）→ 尾部不完整行（无换行结尾）作为最后一帧。

这样普通行不会被撕开，同时内存有确定上界。固有的损失（所有策略共有）：
**跨流的真实时间顺序不可恢复**——帧流只记录"到达顺序"的近似（受管道缓冲
和进程调度影响）；单流内部的字节顺序严格保持。

## 帧格式（可逆的关键）

```
帧 = 标签(1 字节) + b' ' + 长度(ASCII 十进制) + b'\n' + 负载(长度字节)
标签: b'1' = stdout, b'2' = stderr
```

负载是原始字节、不转义；长度前缀使任意二进制内容（含 `\n`、帧头样式的
字节）都能安全嵌入。拆分时按标签把负载顺序拼接即逐字节还原原流。

## 边界情形的确定处理

- **无换行结尾**：EOF 时把残余缓冲作为最后一帧发出，不补换行，拆分后
  逐字节还原（`case4_no_trailing_nl`）。
- **超长行**：超过 `max_record` 的行被切成若干 ≤ `max_record` 的帧，
  内容拼接后与原行一致（`case5_long_line`，200 KiB 单行）。
- **空流**：没有任何帧，合并结果为空字节串（`case6_both_empty`）。

## 内存上界

每个流的内部缓冲 < `max_record + read_chunk`（默认 64 KiB + 64 KiB），
两个流合计 < 256 KiB，**与输出总量无关**。拆分侧逐帧读取，单帧负载
同样 ≤ 合并时的 `max_record`。`MemoryBoundTest` 用约 65 MB 输出验证：
合并 + 拆分的 Python 侧峰值内存 < 16 MB（实测约 0.2 MB），且用
SHA-256 校验两流内容零丢失。

## 100 万行基准（bench_stream_merge.py 实测）

环境：Python 3.12.3，Linux x86-64。stdout/stderr 各 50 万行交替，
原始数据 42,000,000 字节。

| 指标 | 结果 |
| --- | --- |
| 合并耗时 | ≈ 2.4 s（约 41 万行/s） |
| 拆分耗时 | ≈ 1.7 s（约 60 万行/s） |
| 合并流大小 | 47,000,000 字节（帧头开销 10.64%，行越短占比越高） |
| Python 侧内存峰值 | 0.18 MB（tracemalloc） |
| 进程 RSS 高水位 | 17.37 MB（含解释器基线，较开始前 +0.4 MB） |
| 正确性 | 拆分后两流 SHA-256 与原始数据一致 |

## 运行命令

```bash
python3 -m unittest test_stream_merge -v   # 自测（12 个用例）
python3 bench_stream_merge.py              # 100 万行基准
python3 gen_testdata.py                    # 重新生成对拍数据
```

库用法：

```python
import stream_merge

with open("merged.bin", "wb") as sink:
    rc = stream_merge.run_merged(["./some_command"], sink)

with open("merged.bin", "rb") as f, \
     open("out.bin", "wb") as o, open("err.bin", "wb") as e:
    stream_merge.split(f, o, e)   # 字节级还原两个原始流
```
