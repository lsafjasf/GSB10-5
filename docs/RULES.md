# 规则说明 (RV001–RV009)

全部规则基于 Python 标准库 `ast` 语法树节点，不做文本/正则匹配。
每条报警定位到**触发节点本身的起始行列**，并附内层最近的函数/类
结构 (`module` / `function:名` / `class:名` / `method:类.方法` /
`nested:外层.<locals>.内层`)。

| 规则 | 检查项 | 依据 | 语法基础 (命中节点) | 定位节点 |
|---|---|---|---|---|
| RV001 | 可变默认参数 | 默认参数在函数定义时只求值一次，可变对象会在多次调用之间共享状态。 | FunctionDef/AsyncFunctionDef 的 args.defaults / args.kw_defaults 中的 List/Dict/Set 字面量，或 list()/dict()/set() 调用。 | 默认参数表达式 (`[]` / `{}` / `dict()` 等) |
| RV002 | 裸 except 捕获 | 裸 except 会捕获包括 KeyboardInterrupt/SystemExit 在内的所有异常，容易掩盖真正的错误。 | ast.ExceptHandler 节点的 type 字段为 None。 | `except:` 处理器节点 |
| RV003 | 未使用的导入 | 未使用的导入增加阅读负担，也会让依赖关系变得不清晰。 | ast.Import / ast.ImportFrom 引入的名字在全文件的 ast.Name 引用中一次都没有出现 (字符串与注释中的文本不参与计数)。 | `import` 语句 |
| RV004 | 与单例做相等比较 | None/True/False 是单例，应使用 is/is not；用 == 比较会触发对象的__eq__，结果可能被重载。 | ast.Compare 中操作符为 Eq/NotEq，且比较两侧任一是值为 None/True/False 的 ast.Constant。 | `==`/`!=` 比较表达式 |
| RV005 | 异常被静默吞掉 | 捕获异常后只有 pass/... 会把失败静默隐藏，使排障困难。 | ast.ExceptHandler.body 仅由 ast.Pass (或仅由 Ellipsis 表达式) 组成。 | `except ... :` 处理器节点 |
| RV006 | 不可达代码 | return/raise/continue/break 之后的语句永远不会执行，通常是遗留或逻辑错误。 | 同一语句列表 (body/orelse/finalbody) 中，终止语句之后仍存在的ast.stmt (定位到第一条不可达语句)。 | 第一条不可达语句 |
| RV007 | 同作用域重复函数定义 | 同一作用域内后定义的同名函数会覆盖先定义的实现，先定义的函数永远无法按名字调用。 | 同一父作用域语句列表中出现两个以上同名 FunctionDef/AsyncFunctionDef (定位到被覆盖的较早定义)。 | 被覆盖的较早 `def` |
| RV008 | 调用 eval/exec | eval/exec 执行任意动态代码，存在注入风险，通常有更安全的替代方案。 | ast.Call 的 func 是 id 为 eval/exec 的 ast.Name。 | `eval(...)`/`exec(...)` 调用表达式 |
| RV009 | 跨文件访问不存在的模块属性 | import 项目内模块后访问它没有在顶层定义的名字，运行期会抛 AttributeError；该规则需要跨文件的语法树索引。 | 先建立所有文件的顶层定义索引 (函数/类/赋值/导入名)，再检查 ast.Attribute(value=Name(模块别名)) 或 ast.ImportFrom 的目标名是否存在于索引中。 | 属性访问或 `from ... import` 语句 |

## 定位原则

- **行/列**取自触发节点的 `lineno`/`col_offset`，而不是外围语句或
  函数定义行 (例外 RV007 定位到被覆盖的较早定义)。
- **结构**通过一次语法树遍历建立 `id(node) -> 结构名` 映射得到。
- 字符串字面量的内容在 AST 中只是 `ast.Constant` 的值，注释会被
  词法分析直接丢弃，因此其中再像问题的文本也不会命中任何节点。

## 已知边界 (诚实声明)

- RV003 不识别仅供动态使用 (如 `getattr`/插件加载) 的导入。
- RV006 只覆盖同一语句列表中的终止语句之后；`try/else` 的跨块语义
  不分析。
- RV009 只索引顶层名字，不识别运行期动态打补丁 (`mod.x = 1`)，
  也不解析包 (`pkg.mod`) 的多层路径。
