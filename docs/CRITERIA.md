# 可达性判定依据说明

本文档说明本工具对“依赖漏洞是否可达”的判定依据、间接调用的处理方式，
以及每条判定结论背后的理由码。目标是：**只有能从入口逐跳核对的漏洞才报警**，
其余一律给出可复核的不可达理由。

## 1. 总体模型

1. 用 `ast` 对应用与依赖源码做静态解析，构建**函数级调用图**
   （节点 = 函数/方法/模块顶层代码，边 = 带文件与行号的调用点）。
2. 从**入口集合**出发做 BFS，得到可达函数闭包。
3. 对漏洞清单中的每个**漏洞符号**（点分路径，如 `vulnpkg.core.parse`）判定：
   符号落在可达闭包内 → `reachable`，并输出完整调用链；
   否则按 §5 的理由码给出 `unreachable` / `potentially_reachable` /
   `dependency_missing` / `unknown`。

该模型与学术界“依赖级告警大量误报、需做调用级可达性”的结论一致：
Pashchenko 等人在 ESEM 2018 的研究表明，多数项目虽依赖含漏洞的库，
但漏洞代码路径实际不可达，依赖级告警会显著高估风险
（*Vulnerable Open Source Dependencies: Counting Those That Matter*,
ESEM 2018；后续方法学见 *Vuln4Real*, IEEE TSE）。
工业界对应实现可参考 Eclipse Steady（SAP 开源的 Java 调用级可达性分析，
https://github.com/eclipse-steady/steady ）。

## 2. 入口集合

默认入口（自动识别，可用 `--entry module.func` 显式追加/覆盖）：

- 所有名为 `main` 的函数；
- 含 `if __name__ == "__main__":` 守卫的模块顶层代码；
- 合成节点 `<config>`：配置文件解析出的点分路径（见 §4.3），
  因为配置在运行时被加载，视为入口。

入口是分析的根。入口选得越准，误报越少；漏配入口会导致假阴性，
因此 CLI 在 `summary.json` 中回显实际使用的入口集合供核对。

## 3. 调用边的识别规则

| 边类型 | 触发形式 | 处理方式 |
| --- | --- | --- |
| `static` | `foo()`、`mod.foo()` | 经模块 import 表 + 本地函数表做最长前缀解析 |
| `method_heuristic` | `obj.foo()`、`foo().bar()` | 接收者无法静态定型时，按方法名做**保守过近似**匹配（可能误报，不会漏报） |
| `dynamic_import` | `importlib.import_module("a.b")`、`__import__("a.b")`、别名 `m = import_module("a.b")` | 字面量可解析 → 建边到目标模块顶层代码 |
| `reflection` | `getattr(obj, "name")()`、`f = getattr(obj, "name"); f()` | 字面量函数名 → 按名字建边 |
| `config` | 配置文件中的点分路径字符串（如 `"handler": "pkg.mod.func"`） | 能解析到已知符号 → 从 `<config>` 建边 |

每条边都记录 `文件:行号`，可达路径中的每一跳都可回源码核对。

## 4. 间接路径（反射 / 动态导入 / 配置驱动）的处理

### 4.1 字面量可解析
直接建边并标注类型（`dynamic_import` / `reflection` / `config`）。
判定为可达时，调用链中会显示该跳的类型，例如：

```
main.main -> dynpkg.loader.<module>  [dynamic_import] app/main.py:10
dynpkg.loader.<module> -> dynpkg.loader.serve  [static] deps/dynpkg/loader.py:5
```

### 4.2 字面量不可解析（保守策略）
`import_module(变量)`、`getattr(obj, 变量)` 无法静态确定目标，
记入 `dynamic_notes`。若某漏洞符号静态不可达，但**可达函数中存在
未解析的动态分发**，则判为 `potentially_reachable`
（理由码 `unresolved_dynamic_dispatch`），并在 evidence 中列出
具体位置与表达式。宁可多报一条“潜在”，也不静默漏掉。

### 4.3 配置驱动调用
`--config` 指定的 JSON 配置中，所有形如 `a.b.c` 的字符串值都会被提取；
能解析到调用图中已知符号的，建 `config` 边。这是保守近似：
配置字符串不一定都是类路径，但只有能在代码中找到对应符号的才会成边。

## 5. 判定结论与理由码

| status | reason | 含义 |
| --- | --- | --- |
| `reachable` | `path_from_entry_exists` | 存在入口到符号的完整调用链，逐跳给出 |
| `potentially_reachable` | `unresolved_dynamic_dispatch` | 静态不可达，但可达代码含未解析动态分发 |
| `unreachable` | `never_referenced` | 符号在全项目中无任何调用点（无入边） |
| `unreachable` | `no_path_from_entry` | 符号被引用，但引用点不在入口可达闭包内，evidence 列出引用者 |
| `unreachable` | `blocked_by_condition` | 所有路径都经过可静态求值为假的 `if` 分支，evidence 给出守卫表达式与位置 |
| `unreachable` | `symbol_not_found` | 依赖源码中不存在该符号（版本不含或名称有误） |
| `dependency_missing` | `dependency_not_installed` | 依赖既不在清单中也无源码，漏洞不可能触达 |
| `unknown` | `dependency_sources_not_provided` | 依赖已安装但未提供源码，需 `--deps` 补充 |

### 条件挡死的判定
仅对**可安全静态求值**的守卫下结论：字面量常量、模块级常量名
（`FLAG = False`）、布尔运算、常量间比较。求值三态
（真/假/未知），未知时按普通边处理，不会误判为挡死。
实现见 `reachability/conditions.py`，语义依据 Python 语言参考中
常量表达式与布尔运算的定义（https://docs.python.org/3/library/ast.html ）。

## 6. 依据来源

- Python 官方文档 `ast`：抽象语法树解析，全部调用点识别的基础
  （https://docs.python.org/3/library/ast.html ）
- Python 官方文档 `importlib`：`import_module` 动态导入语义
  （https://docs.python.org/3/library/importlib.html ）
- Pashchenko, Plate, Ponta 等：*Vulnerable Open Source Dependencies:
  Counting Those That Matter*（ESEM 2018）——依赖级告警高估风险，
  需调用级可达性
- Pashchenko 等：*Vuln4Real: A Methodology for Counting Actually
  Vulnerable Dependencies*（IEEE TSE）——“实际可达漏洞”计数方法学
- Eclipse Steady（SAP/ Eclipse 基金会开源项目）——工业级调用图
  可达性分析参考实现（https://github.com/eclipse-steady/steady ）
- OWASP Dependency-Check——依赖级检测基线，本工具在其之上补充
  可达性维度（https://owasp.org/www-project-dependency-check/ ）

## 7. 已知局限（有意为之的取舍）

- 方法调用按名字过近似匹配：可能把同名不同类的方法连成边
  （偏向多报可达，不会漏报）；
- 嵌套函数、装饰器、`__getattr__` 元编程、C 扩展不做建模；
- 条件求值只覆盖常量表达式，不跨函数传播常量；
- 分析目标是源码；已安装但未提供源码的依赖只能给出 `unknown`。
