# 令牌作用域校验 —— 语法、包含规则与交付说明

仅依赖 Python 3 标准库。文件：

- `scope.py`：库（解析、合法性、包含判定，fail-closed）
- `test_scope.py`：自测（正常授权 / 越权 / 非法作用域）
- `bench.py`：100 万次校验的耗时与内存基准

## 1. 语法（EBNF）

```
scope    := segment ( "." segment )*
segment  := "*" | LITERAL
LITERAL  := [a-z][a-z0-9_-]{0,62}      # 长度 1..63
```

- 段分隔符固定为 `.`；区分大小写（只允许小写）。
- `*` 只能作为**完整的一段**：`files.*` 合法；`files.read*`、`files.**`、`files.*x` 均非法。
- 段数上限 256；非字符串、空字符串、前导/尾部/连续点、空白、大写、数字开头、段超长均拒绝。

## 2. 包含关系 covers(G, R)（逐项、段级判定）

G = 令牌已授权作用域，R = 本次请求作用域。**按 `.` 切成段后逐段比较，绝不使用字符串前缀**：

1. `len(segments(G)) <= len(segments(R))` —— G 必须是 R 的**段级前缀**（上级覆盖下级，反向不成立）；
2. 对 G 的每一段 `g_i` 与 R 同位置段 `r_i`：`g_i == r_i` 或 `g_i == "*"`（`*` 匹配任意一个段，不跨段）。

两者同时满足才放行；任一端作用域非法即拒绝并给出原因。

关键推论（与字符串前缀的区别）：

- `"files.read"` **不包含** `"files.readonly"`：段名 `read != readonly`，不是比较到字符串长度为止。
- 子级授权不覆盖上级：`"files.read"` 不包含 `"files"`（规则 1 反向不成立）。
- `"files.*"` 不包含 `"files"`：通配段必须匹配到一个真实段。

## 3. 判定表

### 3.1 必须放行（正常授权，12/12 通过，100.0%）

| 情形 | 授权 G | 请求 R | 结果 |
|---|---|---|---|
| 精确匹配 | `files.read` | `files.read` | 放行 |
| 精确匹配 | `*` | `*` | 放行 |
| 上级作用域 | `files` | `files.read` | 放行 |
| 上级作用域 | `files` | `files.read.chunk` | 放行 |
| 上级作用域 | `files.read` | `files.read.chunk` | 放行 |
| 上级作用域 | `a.b.c` | `a.b.c.d.e` | 放行 |
| 根通配 | `*` | `anything.deep.here` | 放行 |
| 通配作用域 | `files.*` | `files.read` | 放行 |
| 通配作用域 | `files.*` | `files.read.chunk` | 放行 |
| 通配作用域 | `files.*.read` | `files.doc.read` | 放行 |
| 通配作用域 | `files.*` | `files.*` | 放行 |
| 精确匹配（单段） | `files` | `files` | 放行 |

### 3.2 必须拒绝（越权用例，19/19 拒绝，100.0%）

| 类别 | 授权 G | 请求 R | 拒绝原因 |
|---|---|---|---|
| 前缀互含 | `files.read` | `files.readonly` | 段名 `read`≠`readonly` |
| 前缀互含 | `files.readonly` | `files.read` | 段名不等 |
| 前缀互含 | `app` | `app2.read` | 段名 `app`≠`app2` |
| 前缀互含 | `files.read` | `files.read2.chunk` | 段名不等 |
| 前缀互含（子→父） | `files.read` | `files` | G 段数 > R，下级不能覆盖上级 |
| 前缀互含（深→浅） | `files.read.chunk` | `files.read` | G 段数 > R |
| 通配混用 | `files.*` | `files` | 通配段必须匹配一个真实段 |
| 通配混用 | `files.*.read` | `files.read` | 通配不跨段（段数对位不符） |
| 通配混用 | `files.*` | `other.read` | 字面段 `files`≠`other` |
| 通配混用 | `files.read*` | `files.readonly` | 授权非法：`*` 不得与字符混用 |
| 通配混用 | `files.**` | `files.read` | 授权非法：`**` 不是完整通配段 |
| 通配混用 | `files.*x` | `files.ax` | 授权非法：`*x` 不得与字符混用 |
| 拼写近似 | `files.read` | `files.raed` | 段名不等（字母换位） |
| 拼写近似 | `files.read` | `files.read1` | 段名不等（尾部加字符） |
| 拼写近似 | `files.read` | `files.reaad` | 段名不等（重复字母） |
| 拼写近似 | `files.read` | `Files.read` | 请求非法：只允许小写 |
| 拼写近似 | `files.read` | `files.read ` | 请求非法：含空白 |
| 拼写近似 | `files.read` | `files.read␉` | 请求非法：含制表符 |
| 拼写近似 | `files.read` | `files．read` | 请求非法：全角点不是分隔符 |

### 3.3 非法 / 空作用域（拒绝 + 原因，13 类全部拒绝）

| 输入 | 拒绝原因 |
|---|---|
| `""` | 作用域为空字符串 |
| `"."` | 第 1 段为空（前导/尾部/连续点） |
| `"files."` | 第 2 段为空（尾点） |
| `".files"` | 第 1 段为空（前导点） |
| `"files..read"` | 第 2 段为空（连续点） |
| `"files.read*"` | `*` 只能作为完整的一段，不能与其他字符混用 |
| `"**"` | `*` 不能与其他字符混用 |
| `"Files"` | 区分大小写，只允许小写字母 |
| `"1abc"` | 段必须以小写字母开头 |
| `"files.read chunk"` | 段中不允许空白字符 |
| `"a"*64`（64 字符） | 段长度超过上限 63 |
| `None` / `123` / `["files"]` | 作用域必须是字符串 |

非法作用域无论出现在授权方还是请求方，`covers()` 返回 `False`，`check()` 返回带原因的
`CheckResult(allowed=False, reason=...)`（fail-closed，绝不因解析异常放行）。

## 4. 性能：100 万次校验

环境：Python 3.12.3 / Linux x86_64。负载为 6 条混合用例（精确、上级、通配、越权拒绝、
非法授权、根通配）循环，每次都重新解析两端字符串（未缓存解析结果，为最保守口径）。
耗时与内存分两轮测量：`time.perf_counter()` 计时轮不挂 tracemalloc，内存轮单独跑。

| 指标 | 第 1 次 | 第 2 次 | 第 3 次 |
|---|---|---|---|
| 总耗时 | 1.058 s | 1.078 s | 1.093 s |
| 单次平均 | 1.058 µs | 1.078 µs | 1.093 µs |
| 吞吐量 | 945,440 次/s | 927,514 次/s | 915,128 次/s |

- Python 堆瞬时峰值（tracemalloc，100 万次循环中任一时刻的新增分配）：**1.7 KiB**（判定过程
  不持有随调用次数增长的状态，内存恒定）。
- 进程 RSS 峰值（`ru_maxrss`，含解释器与全部代码）：**约 32 MiB**。

## 5. 运行命令

```bash
cd scope_check
python3 test_scope.py -v   # 自测：8 个测试类用例全部 OK
python3 bench.py           # 100 万次基准（可加参数：python3 bench.py 10000000）
```

库用法：

```python
from scope import covers, check, parse_scope, ScopeError

covers("files.read", "files.readonly")          # False，bool 快速接口
check("files.read*", "files.read")              # CheckResult(False, "授权作用域非法: ...")
try:
    parse_scope("")
except ScopeError as exc:
    print(exc)                                  # 作用域为空字符串
```
