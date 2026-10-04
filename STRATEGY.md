# streammerge — 子进程输出流合并方案说明

## 1. 问题

把子进程的 stdout 和 stderr 直接 `2>&1` 合到同一个终端/文件时，两个流各自
经管道缓冲后交错写出，同一行可能从中间被撕开，且合并结果无法还原出原始的
两个流。本方案在父进程侧用两个独立管道分别读取，再以**带流标签的分块帧
（framed chunks）**写入一个合并流，使合并结果**无损、可拆分、内存有界**。

## 2. 合并策略对比

| 策略 | 做法 | 损失 / 代价 |
| --- | --- | --- |
| 按时间（`2>&1` 直通） | 内核按写就绪顺序自然交错 | ① 不可拆分，来源信息永久丢失；② 行可能被从中间撕开；③ 跨流顺序受调度影响，不可复现 |
| 按行（行首打标签） | 逐行读，每行前缀 `[out]`/`[err]` | ① 无换行结尾的输出会被"挂起"，要么延迟到流关闭才出现，要么需要造一个假换行（改变字节内容）；② 二进制/超长行无界缓冲；③ 行序在管道缓冲粒度内仍可能交叉 |
| **按块（本方案）** | selector 轮询两个管道，每次最多读 `CHUNK_SIZE`（默认 64 KiB）字节，打成 `(tag, len, payload)` 帧顺序写出 | ① 跨流顺序只能保证到**块粒度**（≤64 KiB），块内不反映两流真实交错的更细顺序——这是任何事后合并都无法恢复的，因为内核管道本身就不记录字节级交错时序；② 每块 5 字节帧头开销（实测 16 MB 负载仅 +1263 字节，0.008%） |

按块策略**没有任何信息损失**：帧里保存的是原始字节和来源标签，拆分后逐
字节等于原流。唯一的"损失"是跨流排序精度被限定在块粒度，以及微小的帧头
开销——这两点在上表中已如实列出。

## 3. MOX1 帧格式

```
文件头:  "MOX1" (4 字节魔数)
帧序列:  [tag:1][length:4 大端][payload:length]  × N
结尾帧:  tag=3, length=4, payload=int32 大端有符号退出码
         （正数=退出码，负数=被信号 -n 杀死）
tag: 1=stdout, 2=stderr, 3=exit
约束: 数据帧 payload ≤ CHUNK_SIZE（默认 65536）
```

- 拆分：顺序读帧，按 tag 写回对应流即可，逐字节还原（`split()`）。
- 损坏检测：魔数错、未知 tag、帧头/负载截断都会抛 `FormatError`。

## 4. 边界情形的确定处理

| 情形 | 处理 | 结果 |
| --- | --- | --- |
| 无换行结尾 | 按块策略不依赖换行符；流 EOF 时残余字节作为最后一个（可能更小的）帧写出 | 拆分后尾部字节**原样保留**，不补换行、不丢失（测试 `test_no_trailing_newline`，对拍数据 `examples/` 中两流均以无换行内容结尾） |
| 超长行 | 单帧负载上限 `CHUNK_SIZE`，超长内容被确定性切成同一 tag 的连续多帧 | 拆分后自动拼回原行；每帧 ≤ chunk_size 有测试断言（`test_long_line_frame_bound`，100 万字节行 + 1024 字节块 → 977 帧） |
| 空流 | 不产生数据帧，仅有 exit 帧 | 拆分得到空文件（`test_empty_streams`） |
| 二进制内容 | 帧是字节级容器，不做任何文本解释 | `\x00`、`\xff` 等任意字节往返一致（`test_binary_and_zero_chunks`） |

## 5. 内存上界

合并与拆分都是**纯流式**的：

- 合并侧每次 `read1(chunk_size)` 后立即写帧，常驻内存 ≈ `CHUNK_SIZE` + 5 字节帧头缓冲，与输出总量无关；
- 拆分侧逐帧读写，常驻内存同样 ≈ `CHUNK_SIZE`；
- 管道容量（Linux 默认 64 KiB）天然背压，子进程不会被父进程拖出无界缓冲。

**内存上界测试**（`tests/test_streammerge.py::TestMemoryBound`）：
同一进程内分别以 2 MB/流 和 64 MB/流 的负载跑合并（输出到 `/dev/null`），
用 `tracemalloc` 测 Python 分配峰值：

```
2MB/流   峰值 229,142 字节
64MB/流  峰值 228,398 字节   （放大 32 倍，delta = -744 字节）
```

断言：两规模峰值差 < 1 MB，且 64 MB/流 时绝对峰值 < 4 MB。实测峰值与规模
完全无关，证明内存有界。100 万行基准中进程峰值 RSS（含解释器）约
16 MB，Python 对象峰值约 219 KB。

## 6. 100 万行基准（实测数据）

环境：Python 3.12.3，Linux 6.18 (WSL2 x86_64)，`CHUNK_SIZE=65536`。
负载：确定性生成器，stdout/stderr 各 50 万行、每行 16 字节（共 16 MB），
按 2 万行为一批交替写两个流。

```
合并（含子进程运行）   ≈ 0.19–0.21 s（多次运行：0.190 / 0.194 / 0.195 / 0.203）
合并吞吐               ≈ 80 MB/s（16,000,000 字节负载）
合并文件大小           16,001,263 字节（帧头开销 1,263 字节 = 0.008%，251 帧）
拆分（合并文件→两个文件） 0.006 s（≈ 2.6 GB/s）
拆分（→/dev/null）      0.002 s（≈ 9.9 GB/s）
峰值 RSS               ≈ 16,000 KB（含 Python 解释器，进程生命周期峰值）
Python 对象峰值        ≈ 219,000 字节（tracemalloc，多次运行 219,124–219,225）
拆分后逐行流式校验     通过（100 万行全部与生成器期望一致）
```

复现：`python3 bench.py --lines 1000000`

## 7. 拆分对拍数据

`examples/` 下是确定性对拍数据（由 `examples/make_examples.py` 生成，
合并时刻意用 16 字节小块以展示分帧）：

- `expected.stdout` / `expected.stderr`：期望的原始两流（含交替块与无换行结尾）
- `merged.mox1`：真实子进程合并产物（23 帧）
- `SHA256SUMS`：两个期望流的 sha256

对拍方法（拆分结果与期望流逐字节一致）：

```bash
D=$(mktemp -d)
python3 streammerge.py split -i examples/merged.mox1 \
    --stdout "$D/expected.stdout" --stderr "$D/expected.stderr"
cd "$D" && sha256sum -c /path/to/repo/examples/SHA256SUMS
# expected.stdout: OK / expected.stderr: OK
```

注意：`merged.mox1` 的**帧交错顺序**依赖运行时调度，不保证逐次一致；
但**拆分还原出的两个流**是确定的，与期望流逐字节相等——这正是本方案的
正确性定义。

## 8. 运行命令

```bash
# 自测（17 个用例：单流/交替/无换行结尾/超长行/二进制/退出码/信号/
#                格式损坏/内存上界/CLI 冒烟）
python3 -m unittest discover -s tests -v

# 100 万行基准（耗时 + 内存峰值 + 拆分校验）
python3 bench.py --lines 1000000

# 合并任意命令的两个输出流
python3 streammerge.py merge -o merged.mox1 -- <命令> [参数...]

# 拆回两个流（返回子进程退出码）
python3 streammerge.py split -i merged.mox1 --stdout out.bin --stderr err.bin

# 重新生成对拍数据
python3 examples/make_examples.py
```

## 9. 文件清单

```
streammerge.py            库 + CLI（FrameWriter/frames_iter/split/merge_pipes/merge）
bench.py                  基准：确定性生成器、流式校验器、耗时与内存报告
tests/test_streammerge.py 17 个自测用例
examples/                 拆分对拍数据（期望流 + 合并文件 + 校验和）
STRATEGY.md               本文档
```
