# 拒绝清单（Rejection List）

由 `tools/gen_rejections.py` 生成；所有位置均为 1 起始的 `行:列`。

## 解析期拒绝（恶意构造 / 语法错误 / 资源限制）

| # | 表达式 | 意图/问题 | 错误分类 | 错误码 | 位置 | 信息 |
|---|--------|-----------|----------|--------|------|------|
| 1 | `os.system('id')` | 命令执行：属性访问 + 函数调用 | security | `E_FORBIDDEN` | 1:3 | attribute access is not allowed |
| 2 | `__import__('os').system('id')` | 模块导入 + 命令执行 | security | `E_FORBIDDEN` | 1:11 | function calls are not allowed |
| 3 | `globals()` | 内建函数调用 | security | `E_FORBIDDEN` | 1:8 | function calls are not allowed |
| 4 | `eval('1 + 1')` | 动态求值调用 | security | `E_FORBIDDEN` | 1:5 | function calls are not allowed |
| 5 | `getattr(user)` | 反射型函数调用 | security | `E_FORBIDDEN` | 1:8 | function calls are not allowed |
| 6 | `(lambda: 1)()` | 立即执行函数 | syntax | `E_SYNTAX` | 1:8 | unexpected character ':' |
| 7 | `(f)()` | 括号后函数调用 | security | `E_FORBIDDEN` | 1:4 | function calls are not allowed |
| 8 | `user.__class__` | dunder 属性访问（逃逸沙箱常见手法） | security | `E_FORBIDDEN` | 1:5 | attribute access is not allowed |
| 9 | `user.name == 'alice'` | 普通属性访问 | security | `E_FORBIDDEN` | 1:5 | attribute access is not allowed |
| 10 | `rows[0]` | 下标访问 | security | `E_FORBIDDEN` | 1:5 | subscript access is not allowed |
| 11 | `user['name'] == 'alice'` | 字符串下标访问 | security | `E_FORBIDDEN` | 1:5 | subscript access is not allowed |
| 12 | `age >= 18; rm -rf /` | 语句分隔符 / 多条表达式 | syntax | `E_SYNTAX` | 1:10 | unexpected character ';' |
| 13 | `age >= 18 # comment` | 非法字符 # | syntax | `E_SYNTAX` | 1:11 | unexpected character '#' |
| 14 | `'user input` | 未闭合字符串 | syntax | `E_LITERAL` | 1:1 | unterminated string literal |
| 15 | `age == 1.` | 残缺数字字面量 | syntax | `E_LITERAL` | 1:10 | malformed number: digits required after decimal point |
| 16 | `age == 1e` | 残缺指数数字 | syntax | `E_LITERAL` | 1:10 | malformed number: exponent digits required |
| 17 | `1 < 2 < 3` | 连续比较（未用 and 连接） | syntax | `E_SYNTAX` | 1:7 | chained comparisons are not allowed; use 'and' to combine them |
| 18 | `(age > 1` | 括号未闭合 | syntax | `E_SYNTAX` | 1:9 | expected ')' |
| 19 | `age >=` | 表达式截断 | syntax | `E_SYNTAX` | 1:7 | unexpected end of expression |
| 20 | `` | 空表达式 | syntax | `E_SYNTAX` | 1:1 | unexpected end of expression |
| 21 | `(((((((((((((((((((((((((((((((((((((((((((((...` | 嵌套深度 65（上限 64） | resource-limit | `E_DEPTH` | 1:65 | expression nesting exceeds limit of 64 |
| 22 | `not not not not not not not not not not not n...` | not 链深度 65（上限 64） | resource-limit | `E_DEPTH` | 1:257 | expression nesting exceeds limit of 64 |
| 23 | `age == 1 or active or active or active or act...` | 超长表达式（上限 10000 字符） | resource-limit | `E_LENGTH` | 1:1 | expression length 17012 exceeds limit of 10000 |

## 求值期分类报告（不静默当成假）

| # | 表达式 | 行数据 | 场景 | 错误码 | 位置 | 信息 |
|---|--------|--------|------|--------|------|------|
| 1 | `age > 'beijing'` | `{'age': 20}` | 数字与字符串做大小比较 | `E_TYPE` | 1:5 | operator '>' cannot compare number with string |
| 2 | `age == vip` | `{'age': 20, 'vip': False}` | 数字与布尔做相等比较 | `E_TYPE` | 1:5 | operator '==' cannot compare number with bool |
| 3 | `vip < true` | `{'vip': False}` | 布尔值做大小比较 | `E_TYPE` | 1:5 | operator '<' cannot compare bool with bool |
| 4 | `age and active` | `{'age': 20, 'active': True}` | 逻辑运算符作用于数字 | `E_TYPE` | - | operator 'and' cannot be applied to number |
| 5 | `not age` | `{'age': 20}` | not 作用于数字 | `E_TYPE` | - | operator 'not' cannot be applied to number |
| 6 | `missing == 1` | `{'age': 20}` | 字段不存在 | `E_FIELD` | 1:1 | unknown field 'missing' |
| 7 | `age < 18 and ghost == 1` | `{'age': 20}` | 短路：右侧字段缺失不报错 | `-` | - | 正常求值 = False |

错误码含义：

- `E_FORBIDDEN`（security）：函数调用 / 属性访问 / 下标访问，一律拒绝。
- `E_SYNTAX`（syntax）：词法或语法不合法。
- `E_LITERAL`（syntax）：数字/字符串字面量残缺。
- `E_DEPTH`（resource-limit）：括号或 `not` 嵌套超过 64 层。
- `E_LENGTH`（resource-limit）：表达式超过 10000 字符。
- `E_TYPE`（type-mismatch）：操作数类型不匹配，显式报错。
- `E_FIELD`（unknown-field）：引用了行中不存在的字段。
