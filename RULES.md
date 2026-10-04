# 跨平台路径处理 — 规范化规则与报告

实现：`src/pathnorm.py`（Python 3，仅标准库）
自测：`tests/test_pathnorm.py`（29 个用例）
基准：`bench.py`（10 万条路径）

## 1. 规范化规则表

| 编号 | 规则 | 处理方式 | 示例 |
|------|------|----------|------|
| R1 | 分隔符 | `\` 与 `/` 都识别为分隔符，统一规范为 `/` | `C:\a\b` → `C:/a/b` |
| R2 | 重复分隔符 | 连续分隔符合并为一个；UNC 锚点 `//server/share` 保留 | `/a//b` → `/a/b` |
| R3 | 相对分量 | 删除 `.`；`..` 词法抵消前一普通分量；相对路径开头无法抵消的 `..` 保留；绝对路径下越过根的 `..` 丢弃 | `a/./b/../c` → `a/c` |
| R4 | 末尾分隔符 | 非根目录的末尾分隔符去除；根 `/`、`C:/` 保留 | `/a/b/` → `/a/b` |
| R5 | 盘符/UNC | 盘符大小写统一为大写（`c:` → `C:`）；UNC 主机与共享保留 | `c:/a` → `C:/a` |
| R6 | 大小写 | `case_sensitive=False` 时比较键做 NFC 归一化 + `casefold`；展示形式 `display` 原样保留大小写 | 见第 3 节 |
| R7 | 保留名 | Windows 语义下分量主名命中 `CON PRN AUX NUL COM1-9 LPT1-9`（不区分大小写，含扩展名如 `con.txt`）即抛 `ReservedNameError`；POSIX 语义下这些名字合法 | `C:/tmp/con.txt` → 报错 |
| R8 | 超长路径 | Windows 上限 260（`\\?\` 扩展前缀时 32767），POSIX 上限 4096；超限抛 `PathTooLongError`，错误信息含实际长度与上限，**绝不静默截断** | 见第 4 节 |
| R9 | 空路径 / NUL | 空串抛 `EmptyPathError`；含 `\x00` 抛 `PathNormError` | — |

边界说明：`C:foo`（盘符相对路径）按 `C:/foo` 处理；规则全部为**纯词法**解析，不访问文件系统，因此行为在任何平台上都确定一致。

## 2. 等价性判定数据

两个路径当且仅当规范化后的比较键（`NormalizedPath.key`）相同时等价。
数据与 `tests/test_pathnorm.py::TestEquivalenceTable.CASES` 一一对应：

| # | 路径 A | 路径 B | style | 大小写敏感 | 等价 | 命中规则 |
|---|--------|--------|-------|-----------|------|----------|
| 1 | `C:\Users\Alice\f.txt` | `C:/Users/Alice/f.txt` | windows | 否 | ✅ | R1 纯分隔符差异 |
| 2 | `C:\Users/Alice\f.txt` | `C:/Users/Alice/f.txt` | windows | 否 | ✅ | R1/R2 混用分隔符 |
| 3 | `C:/Users/ALICE/f.TXT` | `c:/users/alice/f.txt` | windows | 否 | ✅ | R5/R6 大小写差异 |
| 4 | `/home/Alice` | `/home/alice` | posix | 是 | ❌ | R6 敏感文件系统 |
| 5 | `/home/Alice` | `/home/alice` | posix | 否 | ✅ | R6 不敏感文件系统 |
| 6 | `a/./b/../c` | `a/c` | posix | 是 | ✅ | R3 相对分量 |
| 7 | `C:/a/b/` | `C:/a/b` | windows | 否 | ✅ | R4 末尾分隔符 |
| 8 | `\\srv\share\d` | `//srv/share/d` | windows | 否 | ✅ | R1 UNC |
| 9 | `/x//y///z` | `/x/y/z` | posix | 是 | ✅ | R2 重复分隔符 |
| 10 | `C:/a/b` | `C:/a/c` | windows | 否 | ❌ | 不同路径 |
| 11 | `../a` | `a` | posix | 是 | ❌ | R3 不同相对基准 |

## 3. 大小写敏感 / 不敏感对照结果

同一对路径 `/home/Alice` 与 `/home/alice`（或任意盘符路径）：

| 配置 | 比较键 A | 比较键 B | 判定 |
|------|----------|----------|------|
| `style='posix', case_sensitive=True`（ext4 等） | `/home/Alice` | `/home/alice` | 不等价 |
| `style='posix', case_sensitive=False`（macOS HFS+/FAT 挂载等） | `/home/alice` | `/home/alice` | 等价 |
| `style='windows', case_sensitive=False`（NTFS 默认） | `c:/users/alice` | `c:/users/alice` | 等价 |
| `style='windows', case_sensitive=True` | `C:/Users/Alice` | `C:/users/alice` | 不等价 |

注意：`display` 字段始终保留原始大小写（`C:/Users/Alice`），只有比较键 `key`
随敏感性变化，从而避免"规范化把用户原始写法吃掉"。

## 4. 保留名与超长路径处理报告

| 输入 | style | 结果 |
|------|-------|------|
| `C:/dir/CON`、`con`、`LPT3`、`COM9` | windows | `ReservedNameError`（主名大小写不敏感） |
| `C:/dir/con.txt` | windows | `ReservedNameError`（扩展名剥离后仍命中） |
| `C:/dir/conversation.txt` | windows | ✅ 通过（仅前缀相同不算保留名） |
| `/tmp/CON` | posix | ✅ 通过（保留名规则只作用于 windows 语义） |
| `C:/` + 300 字符 | windows | `PathTooLongError: 长度 303 超过 260` |
| `\\?\C:\` + 300 字符 | windows | ✅ 通过（扩展前缀放宽至 32767） |
| `\\?\C:\` + 33000 字符 | windows | `PathTooLongError: 超过 32767` |
| `/` + 5000 字符 | posix | `PathTooLongError: 超过 4096` |
| `""`（空） | — | `EmptyPathError` |
| 含 `\x00` | — | `PathNormError` |

所有拒绝均为确定性异常，包含长度/分量信息，不存在任何静默截断分支。

## 5. 10 万条路径耗时与内存（实测）

机器：Python 3.12.3 / Linux，数据由固定随机种子生成（windows/posix 各半，
混合 `\` `/`、大小写、`.` `..`、含保留名分量与中文路径）。

| 指标 | 数值 |
|------|------|
| 样本 | 100,000 条 |
| 规范化成功 / 校验拒绝 | 85,825 / 14,175（拒绝均为命中 CON 等保留名） |
| 规范化总耗时 | ≈ 1.63 s |
| 单条耗时 | ≈ 16.3 µs/条 |
| 等价性判定（10 万对，含分隔符+大小写变体） | ≈ 0.37 s，等价命中 85,825 |
| `tracemalloc` 内存峰值 | ≈ 48.0 MiB（保留全部结果对象） |
| 进程 RSS 峰值 | ≈ 152.7 MiB |

数字随机器浮动，重新运行 `python3 bench.py` 即可复现。

## 6. 运行命令

```bash
# 自测（29 用例：分隔符/混用/大小写/保留名/超长路径/等价表）
python3 tests/test_pathnorm.py -v

# 10 万条路径性能基准
python3 bench.py
```

API 速览：

```python
from pathnorm import normalize, equivalent

normalize(r"C:\Users\Alice\f.TXT", style="windows", case_sensitive=False).display
# -> 'C:/Users/Alice/f.TXT'
equivalent("C:/USERS/alice/f.txt", r"c:\users\Alice\f.TXT",
           style="windows", case_sensitive=False)   # -> True
```
