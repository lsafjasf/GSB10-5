# 过滤表达式语言：语法说明与实现说明

## 1. 设计目标

用户输入的过滤条件**绝不进入** `eval`/`exec`/`compile` 等任何语言自带求值
通道。整条链路为：手写词法分析 → 手写递归下降语法分析 → AST → 手写树遍历
求值。表达式只能描述"对一行数据的布尔判断"，无法调用函数、访问属性、执行
语句。

## 2. 词法

| 类别 | 规则 |
|------|------|
| 空白 | 空格、制表、换行，仅作分隔 |
| 数字 | `123`、`12.5`、`.5`、`1e3`、`1.5e-2`（int 或 float） |
| 字符串 | 单/双引号，转义：`\n \t \r \0 \\ \" \' \/ \b \f \xNN \uNNNN` |
| 标识符 | `[A-Za-z_][A-Za-z0-9_]*`，表示数据行的字段名 |
| 关键字 | `and` `or` `not` `true` `false` `null`（小写） |
| 运算符 | `==` `!=` `<` `<=` `>` `>=` `(` `)` 及一元负号 `-` |

其他一切字符（`;` `=` `!` `&` `|` 等）在词法层即报语法错误并给出位置。
`.` `[` `]` 能被切分，但会在语法层按"属性访问/下标访问"安全拒绝。

## 3. 文法（EBNF）

```
expr     := or_expr
or_expr  := and_expr ( "or"  and_expr )*
and_expr := not_expr ( "and" not_expr )*
not_expr := "not" not_expr | cmp_expr
cmp_expr := unary ( CMP_OP unary )?        # 不允许链式比较 a < b < c
unary    := "-" unary | primary
primary  := NUMBER | STRING | "true" | "false" | "null"
          | IDENT                          # 字段引用
          | "(" or_expr ")"
CMP_OP   := "==" | "!=" | "<" | "<=" | ">" | ">="
```

## 4. 优先级与结合性

| 优先级（低→高） | 运算符 | 结合性 |
|-----------------|--------|--------|
| 1 | `or` | 左结合 |
| 2 | `and` | 左结合 |
| 3 | `not` | 右结合（前缀，可连锁 `not not x`） |
| 4 | `==` `!=` `<` `<=` `>` `>=` | 不结合（链式比较被拒绝） |
| 5 | 一元 `-` | 右结合（前缀） |
| 6 | 字面量、字段、括号 | — |

与 Python 表达式优先级逐条对拍，见 `tools/differential.py` 的
`FIXED_CASES`（20 条固定用例）与 `data/differential_cases.jsonl`
（20 固定 + 1000 随机 x 3 行 = 3020 条记录，全部一致）。

## 5. 求值语义

- **字段引用**：行内不存在该 key 时抛 `FieldMissing`
  （category=`field_missing`），绝不静默当假。
- **`==` / `!=`**：允许任意两侧类型；`bool` 不被视作 `int`
  （`True == 1` 为假，与 Python 不同，刻意收紧）；`null` 只等于 `null`；
  类型不同一律不相等（不抛错）。
- **`<` `<=` `>` `>=`**：只允许两侧同为数值（int/float，不含 bool），
  否则抛 `TypeMismatch`（category=`type_mismatch`）。
- **`not`**：操作数必须是 bool，否则 `TypeMismatch`。
- **一元 `-`**：操作数必须是 int/float（不含 bool），否则 `TypeMismatch`。
- **`and` / `or`**：两个操作数必须是 bool，短路求值，结果必为 bool。
- **整体结果**：必须是 bool，否则 `TypeMismatch`。

错误分类一览：

| category | 含义 | 阶段 |
|----------|------|------|
| `syntax` | 非法字符、括号不匹配、残留内容等 | 编译期 |
| `security` | 函数调用、属性访问、下标访问、超长、超深嵌套 | 编译期 |
| `field_missing` | 字段不存在 | 求值期 |
| `type_mismatch` | 运算符两侧类型不匹配 | 求值期 |

## 6. 安全闸门（默认值，可调）

| 闸门 | 默认值 | 触发异常 |
|------|--------|----------|
| 表达式长度 | 10000 字符 | `ExpressionTooLong` |
| 嵌套深度（括号 / not / 一元负号计层） | 50 层 | `NestingTooDeep` |
| 函数调用（标识符或表达式后紧跟 `(`） | 一律拒绝 | `FunctionCallNotAllowed` |
| 属性访问（`.`） | 一律拒绝 | `AttributeAccessNotAllowed` |
| 下标访问（`[` `]`） | 一律拒绝 | `SubscriptNotAllowed` |

所有拒绝都携带 1 起始的 行:列 位置，清单见 `REJECTIONS.md`。

## 7. 实现说明

| 模块 | 职责 |
|------|------|
| `filterexpr/lexer.py` | 手写扫描器，输出 token 流（类型、值、offset、行、列） |
| `filterexpr/parser.py` | 手写递归下降 parser，深度/长度闸门，产出元组 AST |
| `filterexpr/nodes.py` | AST 节点定义（元组，tag + payload + 位置） |
| `filterexpr/evaluator.py` | 手写树遍历求值，严格类型检查与短路 |
| `filterexpr/api.py` | `compile()` / `Expression`，编译一次多次求值 |
| `filterexpr/errors.py` | 统一异常体系与 category 分类 |

AST 用元组而非类，是为了在 10 亿次求值量级下减少属性查找开销。
表达式只编译一次；对每行数据反复调用 `Expression.evaluate(row)`。

## 8. 用法示例

```python
import filterexpr as fx

expr = fx.compile("age >= 18 and (city == 'beijing' or vip == true)")
expr.evaluate({"age": 30, "city": "beijing", "vip": False})   # True

# 批量：出错行不静默当假，单独分类上报
results, errors = expr.evaluate_many(rows)
```
