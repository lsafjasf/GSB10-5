# 代码评审检查项规则说明

所有规则均基于 Python 标准库 `ast` 语法树判定，不做任何正则/纯文本匹配。
字符串常量（含 f-string 的非插值部分）、注释、文档字符串不会进入判定；
“导入是否被使用”以 AST 中的 `Name` 节点为准，注释/字符串里提到名字不算使用。

每条报警输出：`文件:行:列: 规则编号 [所在结构] 说明`。
所在结构是包含该节点的完整嵌套路径（如 `Svc.run`），模块级为 `<module>`。
行号取自 AST 节点自身的 `lineno`，列号取自 `col_offset`，多行定义精确到问题节点那一行。

| 规则 | 名称 | 判据节点 |
|---|---|---|
| CR001 | 可变默认参数 | `FunctionDef/AsyncFunctionDef.args.defaults / kw_defaults` |
| CR002 | 裸 except | `ExceptHandler.type is None` |
| CR003 | 危险动态执行 | 直接调用 `eval()`/`exec()`，或 `os.system()` |
| CR004 | 未使用导入 | `Import/ImportFrom` 绑定名在模块内无任何 `Name` 引用 |
| CR005 | 动态拼接 SQL | `*.execute/executemany/executescript` 首参为 f-string / `%` / `.format` |
| CR006 | 失效的跨文件导入 | `from A import x` 时 A 是本地模块且未定义 x（跨文件上下文） |
| CR000 | 语法解析失败 | `SyntaxError`（不崩溃，按行报出） |

## CR001 可变默认参数

- 依据：默认参数在函数定义时求值一次，`[]`/`{}`/`set()`/`list()`/`dict()`
  这类可变对象会在多次调用间共享，导致数据串号。
- 判据：默认值节点是 `List`/`Dict`/`Set` 字面量，或目标名为
  `list/dict/set` 的零参 `Call`。`None`、数字、元组、字符串、`frozenset()`
  等不可变默认值不报警。
- 定位：报在默认值表达式所在行（多行签名中也能指到具体那一行），结构为函数全限定名。

## CR002 裸 except

- 依据：`except:` 等价于捕获 `BaseException`，会吞掉
  `KeyboardInterrupt`、`SystemExit`，也会掩盖编码错误。
- 判据：`ExceptHandler.type is None`。`except Exception:`、`except OSError:` 不报警。
- 定位：报在 `except` 行（handler 节点的 `lineno`），结构为所在函数/类。

## CR003 危险动态执行

- 依据：`eval`/`exec` 直接执行字符串即代码注入入口；`os.system` 直接执行
  shell 字符串即命令注入入口。
- 判据：`Call.func` 为 `Name(id in {eval, exec})`，或
  `Attribute(value=Name('os'), attr='system')`。只认直接调用，
  `getattr(x, "eval")` 等不判（避免误报），已知限制见末尾。
- 定位：报在调用表达式所在行，结构为所在函数/类。

## CR004 未使用导入

- 依据：无用导入增加阅读负担，且常是改名/删除后遗漏的死代码。
- 判据：导入绑定名（`import a.b` 绑定 `a`；`import x as y` 绑定 `y`；
  `from m import x` 绑定 `x`）在整棵模块 AST 中没有任何同 `Name` 引用。
  特例：名字出现在 `__all__` 字符串列表中视为显式再导出，不报警。
- 注释和字符串里出现该名字不算使用（不是 `Name` 节点）——这是 AST 方案
  相对文本匹配的关键区别，见 `tests/corpus/string_usage_decoy.py`。
- 定位：报在具体 alias 所在行/列（多名称导入语句可精确定位到第几个名字），结构为 `<module>`。

## CR005 动态拼接 SQL

- 依据：把变量拼进 SQL 字符串再执行是经典 SQL 注入写法。
- 判据：被调用方法名在 `{execute, executemany, executescript}` 中，且
  第一个参数是：`JoinedStr`（f-string）、左操作数任意的 `%` 二元表达式、
  或对字符串调用 `.format()`。占位符 + 参数元组（`execute(sql, (x,))`）不报警。
- 定位：报在 execute 调用所在行，结构为所在函数/类。

## CR006 失效的跨文件导入（跨文件上下文）

- 依据：`from a.b import x` 中 `x` 在本地模块 `a.b` 根本不存在时，
  导入即失败，通常是重构改名后的遗留；单文件内无法判定，需要工程级上下文。
- 判据：先扫描工程内所有 `.py` 建立「模块 → 顶层符号」索引
  （顶层函数/类/赋值目标/导入绑定；`__init__.py` 视为包；支持 `level>0` 相对导入解析）。
  仅当导入目标能解析到本地模块时才判定；符号不存在且不是其下子模块时报警。
  第三方/标准库模块不在索引中，一律不判（避免误报）。
- 定位：报在具体 alias 所在行/列，结构为 `<module>`。

## 已知限制（宁可漏报，不制造误报）

- 动态反射（`getattr(m, name)`、`importlib.import_module`）不判定。
- `if TYPE_CHECKING:` 下、只出现在字符串注解 `-> "X"` 中的导入名会按未使用报
  （可通过使用或 `__all__` 消除，实际使用时再扩展规则）。
- CR006 只索引顶层符号，不在 `__init__.py` 中显式 re-export 的子包符号不报。
