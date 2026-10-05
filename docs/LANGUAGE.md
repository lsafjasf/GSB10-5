# 过滤条件语言说明（safefilter）

面向「用户输入过滤条件」场景的安全布尔表达式语言。解释器自行完成**词法分析、
语法分析与求值**，全程不使用 `eval` / `exec` / `compile` / `getattr` 等宿主
语言求值能力，仅依赖 Python 标准库。

## 1. EBNF 文法

```ebnf
expr        := or_expr
or_expr     := and_expr ( "or"  and_expr )*
and_expr    := not_expr ( "and" not_expr )*
not_expr    := "not" not_expr | comparison
comparison  := primary ( cmp_op primary )?
cmp_op      := "==" | "!=" | "<" | "<=" | ">" | ">="
primary     := NUMBER | STRING | "true" | "false" | "null"
             | IDENT | "(" expr ")"
IDENT       := [A-Za-z_][A-Za-z0-9_]*
NUMBER      := 整数 | 小数 | 科学计数法（如 18、0.95、2.5e-3）
STRING      := 单引号或双引号，支持 \n \t \r \" \' \\ 等转义
```

关键字 `and or not true false null` 均为**小写保留字**；没有取模、位运算、算术
运算、三元表达式与语句分隔符。

## 2. 运算符优先级与结合性

| 层级 | 运算符 | 含义 | 结合性 |
|------|--------|------|--------|
| 1（最低） | `or` | 逻辑或 | 左结合 |
| 2 | `and` | 逻辑与 | 左结合 |
| 3 | `not` | 逻辑非 | 右结合 |
| 4 | `==` `!=` `<` `<=` `>` `>=` | 比较 | **不可结合**（禁止链式比较） |
| 5（最高） | 括号 `( )`、字面量、字段 | 原子 | — |

要点：

- `a or b and c` 解析为 `a or (b and c)`。
- `not a and b` 解析为 `(not a) and b`（`not` 比 `and` 紧）。
- `not not a` 右结合，即 `not (not a)`。
- `a == 1 and b == 2 and c == 3` 左结合，即 `(a==1 and b==2) and c==3`。
- 括号可改变默认优先级：`(a or b) and c`。
- 比较运算符**不可结合**：`1 < 2 < 3` 直接报语法错误（位置 1:7），
  必须写成 `1 < 2 and 2 < 3`，避免与 Python「隐式合取」的歧义语义混淆。

## 3. 类型与求值语义

四种值：`null`、`bool`、`number`（int/float）、`string`。规则严格，不做隐式
真假转换，也不把错误静默当成 `false`：

| 运算 | 合法操作数 | 说明 |
|------|------------|------|
| `and` `or` | 两侧必须是 bool | 短路求值；否则 `E_TYPE` |
| `not` | 操作数必须是 bool | 否则 `E_TYPE` |
| `==` `!=` | 两侧同类型 | int 与 float 同属 number；bool 不并入 number |
| `<` `<=` `>` `>=` | 两个 number 或两个 string | 否则 `E_TYPE` |
| 字段引用 | 字段必须存在于行中 | 否则 `E_FIELD`，并给出源码位置 |

`null` 语义：`null == null` 为真；`null == <非 null>` 为假（不抛错）；
`null` 参与大小比较为类型错误。

顶层表达式求值结果必须是 bool；单独写一个字段（如 `age`）会报 `E_TYPE`。

## 4. 安全模型（解析期拒绝）

以下构造在词法/语法层被识别并拒绝，错误指向触发位置（`行:列`）：

- **函数调用** `f(...)`，含 `__import__`、`eval`、`getattr`、`globals` →
  `E_FORBIDDEN`。
- **属性访问** `a.b`（含 `user.__class__` 这类 dunder 逃逸手法）→
  `E_FORBIDDEN`。
- **下标访问** `a[i]` / `a["k"]` → `E_FORBIDDEN`。
- 语句分隔、注释符等任何文法外字符 → `E_SYNTAX`。
- 残缺数字/字符串字面量 → `E_LITERAL`。
- **嵌套深度**：括号嵌套或 `not` 链超过 **64** 层 → `E_DEPTH`（默认上限，
  可调），直接中断解析，不会爆 Python 递归栈。
- **表达式长度**：超过 **10000** 字符 → `E_LENGTH`（默认上限，可调）。

完整样本见 `docs/REJECTIONS.md`（由 `tools/gen_rejections.py` 生成）。

## 5. 错误分类

所有异常继承 `safefilter.FilterError`，带稳定错误码、`line`、`column` 与
`describe()`（打印带 `^` 指示位的源码片段）：

- `E_SYNTAX` / `E_LITERAL`：syntax
- `E_DEPTH` / `E_LENGTH`：resource-limit
- `E_FORBIDDEN`：security
- `E_TYPE`：type-mismatch（不静默当假）
- `E_FIELD`：unknown-field（不静默当假）

## 6. API

```python
from safefilter import compile_expr

expr = compile_expr('age >= 18 and (vip == true or city == "beijing")')
expr.evaluate({"age": 20, "vip": False, "city": "beijing"})  # True

# 列式存储批量求值（benchmark 使用）
counter = expr.bind_columns({"age": [...], "vip": [...], "city": [...]})
counter(0, 1_000_000)   # 区间 [0, n) 内命中行数

compile_expr(src, max_depth=64, max_length=10000)  # 资源上限可调
```

## 7. 实现说明

- `safefilter/lexer.py`：手写字符循环扫描器，产出
  `(类型, 值, 行, 列)` token 流。
- `safefilter/parser.py`：手写递归下降分析器，按
  `or → and → not → comparison → primary` 分层显式编码优先级；AST 为
  纯 tuple 树；深度计数与禁止构造在此处理。
- `safefilter/evaluator.py`：把 AST 编译为闭包树后**自行解释执行**；运算符
  通过显式标签分派后用硬编码操作实现；`and`/`or` 短路；类型检查先于运算。
