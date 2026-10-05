# 令牌作用域校验：语法、包含规则与验证报告

实现：`scope.py`（Python 3，仅标准库，无第三方依赖）。
自测：`selftest.py`；基准：`bench.py`。

## 1. 作用域语法

```abnf
scope    = resource ":" action
resource = segment *("." segment)
segment  = literal / "*" / "**"
literal  = (ALPHA / DIGIT) *(ALPHA / DIGIT / "-" / "_")   ; 仅小写
action   = "read" / "write" / "readwrite" / "*"
```

- `resource` 是以 `.` 分隔的段序列，描述层级资源（如 `files.docs.2024`）。
- `*`：单层通配段，匹配且仅匹配一个段。
- `**`：多层通配段，**只允许出现在最后一段**，匹配剩余零个或多个段。
- `action` 仅允许 `read` / `write` / `readwrite` / `*`。
- 通配符只允许出现在**令牌持有的授权作用域**中；**被校验的请求作用域必须是具体作用域**，含通配即拒绝。

## 2. 包含（覆盖）规则 —— 逐项判定，不做字符串前缀比较

授权作用域 G 覆盖请求作用域 R，当且仅当以下两条同时成立：

1. **resource 逐段判定**：把两侧 resource 按 `.` 切成段序列后逐段比较：
   - 字面段：两段**完全相等**才算匹配（`files` ≠ `files2`，`docs` ≠ `doc`）；
   - `*`：匹配 R 中**恰好一个**段，不能跨段、不能匹配零段；
   - `**`：匹配 R 中剩余**零个或多个**段（已在校验期保证它只能是末段）；
   - 除 `**` 外，两侧段数必须恰好对齐，多一段少一段都不匹配。
2. **action 蕴含闭包**：`read→{read}`，`write→{write}`，`readwrite→{read, write}`，`*→{read, write}`。
   G 的 action 闭包必须包含 R 的 action。读是读写的真子集：持 `readwrite` 可读可写，持 `read` 不可写。

**失败即拒绝（fail closed）**：作用域为空、结构非法、授权项无法解析时一律拒绝，并给出具体原因。

## 3. 判定表（正常授权，12/12 通过，通过率 100%）

| # | 授权作用域 | 请求作用域 | 结果 | 覆盖情形 |
|---|-----------|-----------|------|---------|
| 1 | `files.docs:read` | `files.docs:read` | ALLOW | 精确匹配 |
| 2 | `files.**:read` | `files:read` | ALLOW | 上级作用域，`**` 匹配零段 |
| 3 | `files.**:read` | `files.docs.2024:read` | ALLOW | 上级作用域，`**` 匹配多段 |
| 4 | `files.*:read` | `files.docs:read` | ALLOW | 单层通配 |
| 5 | `files.*.tmp:read` | `files.docs.tmp:read` | ALLOW | 中段通配 |
| 6 | `files.docs:readwrite` | `files.docs:read` | ALLOW | 读写蕴含读 |
| 7 | `files.docs:readwrite` | `files.docs:write` | ALLOW | 读写蕴含写 |
| 8 | `files.docs:*` | `files.docs:write` | ALLOW | action 通配 |
| 9 | `files.**:readwrite` | `files.docs.secret:write` | ALLOW | 上级 + 读写蕴含写 |
| 10 | `a.b:read, files.**:readwrite, c.d:write` | `files.x:write` | ALLOW | 多授权项命中其一 |
| 11 | `files.**:read, files.docs:write` | `files.docs:write` | ALLOW | 精确项优先于宽授权 |
| 12 | `*:read` | `files:read` | ALLOW | resource 顶层通配 |

## 4. 越权用例结果（17/17 全部 DENY，拦截率 100%）

| # | 类别 | 授权作用域 | 请求作用域 | 结果 | 说明 |
|---|------|-----------|-----------|------|------|
| 1 | 前缀互含 | `files.public:read` | `files.public2:read` | DENY | `startswith` 会误放行，逐段判定不会 |
| 2 | 前缀互含 | `files.publicity:read` | `files.public:read` | DENY | 反向前缀同样拒绝 |
| 3 | 前缀互含 | `files:read` | `files2:read` | DENY | 段名不同即不等 |
| 4 | 前缀互含 | `files:read` | `files.evil:read` | DENY | 无 `**` 不得下潜 |
| 5 | 前缀互含 | `files.docs:read` | `files.docs.tmp:read` | DENY | 段数不齐 |
| 6 | 通配混用 | `files.*:read` | `files.a.b:read` | DENY | `*` 只匹配一段 |
| 7 | 通配混用 | `files.*:read` | `files:read` | DENY | `*` 不能匹配零段 |
| 8 | 通配混用 | `files.*:read` | `files.x:write` | DENY | resource 通配 ≠ action 通配 |
| 9 | 通配混用 | `files.*.tmp:read` | `files.a.b.tmp:read` | DENY | 中段 `*` 不跨段 |
| 10 | 通配混用 | `files.**:read` | `other:read` | DENY | 字面首段不成立 |
| 11 | 通配混用 | `files.*:*` | `files.a.b:read` | DENY | 双通配仍受段数约束 |
| 12 | 拼写近似 | `files.docs:read` | `files.doc:read` | DENY | 少一个字母即不等 |
| 13 | 拼写近似 | `files.docs:read` | `files.docs:reads` | DENY | 非法 action，解析期拒绝 |
| 14 | 拼写近似 | `files.docs:read` | `files.d0cs:read` | DENY | 形近字符不等 |
| 15 | 拼写近似 | `user.settings:write` | `users.settings:write` | DENY | 单复数不同名 |
| 16 | 拼写近似 | `files.docs:readwrite` | `files.docs:rd` | DENY | 非法 action，解析期拒绝 |
| 17 | 权限方向 | `files.docs:read` | `files.docs:write` | DENY | 读不含写（方向不可逆） |

## 5. 空 / 非法作用域（全部拒绝并说明原因）

| 输入 | 拒绝原因 |
|------|---------|
| `""` | 作用域为空字符串 |
| `"   "` | 作用域含首尾空白 |
| `files.docs` | 缺少 `:action` |
| `files.docs:` | action 为空 |
| `:read` | resource 部分为空 |
| `files..docs:read` / `.files:read` / `files.:read` | resource 含空段 |
| `files.**.x:read` | `**` 只能作末段（请求侧还因含通配被拒） |
| `files.*:read`（作为请求） | 请求作用域必须具体，不得含通配符 |
| `files.docs:execute` | 未知 action |
| `Files.docs:read` | 段含大写，非法 |
| `files.docs:read:write` | 冒号多于一个 |
| `files.do cs:read` | 段含空格，非法 |

授权侧同样 fail closed：授权列表中混入非法项时，非法项永不放行、合法项继续生效；
授权列表为空或全部非法时，任何请求一律拒绝。

## 6. 性能（100 万次校验，Python 3.12.3，Linux x86-64）

场景：5 条授权作用域，请求 `files.docs.2024.q3.report:read`。

| 指标 | 第一次 | 第二次 |
|------|--------|--------|
| 端到端（每次解析请求 + 逐条 covers） | 1.709 s（≈58.5 万次/s，1.71 µs/次） | 1.595 s（≈62.7 万次/s，1.60 µs/次） |
| 纯 `covers()` 热路径（双方已解析） | 0.352 s（≈283.7 万次/s，0.35 µs/次） | 0.331 s（≈302.6 万次/s，0.33 µs/次） |
| 100 万次 `check()` 堆峰值增量（tracemalloc） | 7.2 KiB | 7.2 KiB |
| 进程峰值 RSS（含解释器） | 31.8 MiB | 13.0 MiB |

校验本身不累积状态：100 万次循环的堆峰值增量仅约 7 KiB（临时对象即抛即回收），
RSS 差异来自解释器自身分配，与校验次数无关。

## 7. 运行命令

```bash
python3 selftest.py   # 自测：判定表 + 越权用例 + 非法作用域 + 通过率，失败退出码 1
python3 bench.py      # 基准：100 万次校验的耗时与内存峰值
```

库用法：

```python
from scope import check
decision = check(["files.**:readwrite"], "files.docs:read")
decision.allowed        # True
decision.matched_grant  # 'files.**:readwrite'
```
