# 自测结果与数据

环境：Python 3.12.3，Linux，纯标准库。以下全部数字由仓库内脚本实测产出。

## 1. 测试语料是怎么构造的

语料在 `tests/corpus/`，并有人工标注的 `expected.json`
（每条标注包含 rule_id / 文件 / 行号 / 所在结构）：

| 文件 | 构造意图 | 期望报警 |
|---|---|---|
| `real_issues.py` | 每条规则各 1 个真实问题（真实 TP） | 5 条：CR004@3, CR001@6, CR002@16, CR003@21, CR005@25 |
| `decoys.py` | 注释、多行字符串、模块文档字符串里的假问题 | 0 条 |
| `string_usage_decoy.py` | 导入名只出现在注释和字符串里 | 1 条：CR004@2（仍算未使用） |
| `clean.py` | 全部正确写法（不可变默认、`except OSError`、参数化 SQL） | 0 条 |
| `pkg_a/`、`pkg_b/user.py` | 跨文件上下文：存在的符号、不存在的符号、子模块导入 | 2 条：CR006@3, CR004@5 |

构造方法刻意覆盖四类情形：真实问题、字符串里的假问题、注释里的假问题、
跨文件上下文。除语料驱动测试外，`tests/test_rules.py` 还有 10 个单元用例
（不可变默认、嵌套类结构名、`%`/`.format` 两种 SQL 变体、`__all__` 特例、
语法错误不崩溃等）。

## 2. 误报与漏报数据（标注语料）

`python3 -m unittest discover -s tests` 实测：

```
Ran 14 tests ... OK
语料指标: TP=8 FP=0 FN=0 precision=1.000 recall=1.000
```

- 误报 FP = 0：`decoys.py` 中 8 处假模式（注释 3 处、多行字符串 4 处、
  单行字符串 3 处，部分行含多种模式）0 命中；`clean.py` 0 命中。
- 漏报 FN = 0：`expected.json` 标注的 8 条全部命中。
- 结构字段同步校验：报警的所在结构必须与标注一致（如
  `real_issues.py:16` 必须是 `read_config`，单元用例校验了
  `Svc.run` 嵌套结构）。

## 3. 字符串 / 注释假问题的结果

对 `decoys.py` 单独运行：

```
$ python3 -m codereview tests/corpus/decoys.py
共 0 条报警
```

该文件包含 `def f(x=[])`、`except:`、`eval(x)`、`cur.execute(f"...")`、
`from pkg_a.mod import missing_symbol` 等假模式，全部位于注释或字符串中，
0 报警。原因：这些文本是 `Constant`/`Comment`（tokenize 层）而非 AST
代码节点，规则只遍历代码节点。

## 4. 与纯文本（正则）匹配的对比

`python3 tools/validate_locations.py` 在同一份语料上跑一个正则版检查器
（`def ...=[]`、行首 `except:`、`eval(`、`.execute("`）：

| 规则 | 文本匹配误报 | 文本匹配漏报 | AST 误报 | AST 漏报 |
|---|---|---|---|---|
| CR001 | 2（`decoys.py:3` 注释、`decoys.py:8` 字符串） | 0 | 0 | 0 |
| CR002 | 1（`decoys.py:11` 字符串） | 0 | 0 | 0 |
| CR003 | 2（`decoys.py:5` 注释、`decoys.py:10` 字符串） | 0 | 0 | 0 |
| CR005 | 2（`decoys.py:15` 字符串；`clean.py:24` 把参数化 SQL 误判） | 0 | 0 | 0 |

CR004 方向相反：纯文本若“看到名字就算使用”，则
`string_usage_decoy.py` 中 `hashlib` 只出现在注释/字符串时会漏报；
AST 以 `Name` 节点判定，仍在第 2 行正确报警。

## 5. 定位精度校验数据

两层校验：

1. 节点行号一致性：报警行 = AST 节点 `lineno`，结构 = 作用域栈，
   由测试与 `expected.json` 逐字段比对。
2. 物理行锚点校验（`tools/validate_locations.py`）：取报警行物理文本，
   检查是否包含该规则的语法锚点（如 CR002 行必须以 `except` 开头）。

标注语料（8 条）：CR001 1/1、CR002 1/1、CR003 1/1、CR004 3/3、
CR005 1/1、CR006 1/1，合计 8/8 = 100.00%。

10 万行基准（1284 条报警，见下节）锚点校验：

```
CR001: 309/309   CR002: 312/312   CR003: 309/309
CR004: 42/42     CR005: 312/312
合计: 1284/1284 = 100.00%
```

## 6. 10 万行代码耗时与内存峰值

基准工程由 `tools/gen_benchmark.py` 用固定随机种子（20261007）确定性生成：
207 个文件、100,110 行、1284 个注入问题（5 类，位置记录在
`benchmark/manifest.json`），注入行号即期望行号。
`python3 tools/run_benchmark.py benchmark` 两次实测：

| 指标 | 第 1 次 | 第 2 次 |
|---|---|---|
| 规模 | 207 文件 / 100,110 行 | 同左 |
| 检查耗时 | 5.299 s（18,893 行/s） | 4.634 s（21,604 行/s） |
| 内存峰值 tracemalloc | 125.9 MiB | 125.9 MiB |
| 进程峰值 RSS | 315.5 MiB | 315.8 MiB |
| TP / FP / FN | 1284 / 0 / 0 | 1284 / 0 / 0 |
| precision / recall | 1.0000 / 1.0000 | 1.0000 / 1.0000 |
| 行号完全一致率 | 100.00% | 100.00% |

说明：为支持 CR006，检查器会把所有文件 AST 保留在内存建索引，
这是 RSS ~316 MiB 的主因；若改为两趟落盘索引可进一步降低内存。

## 7. 运行命令

```bash
# 检查文件或目录（有报警退出码为 1）
python3 -m codereview tests/corpus

# 自测（14 个用例 + 语料精确比对）
python3 -m unittest discover -s tests

# 定位精度锚点校验 + 文本匹配对比
python3 tools/validate_locations.py

# 生成 10 万行基准工程（确定性）
python3 tools/gen_benchmark.py benchmark 100000

# 基准：耗时 / 内存 / 误报漏报 / 定位精度
python3 tools/run_benchmark.py benchmark
```

## 交付物清单

- 源码：`codereview/checker.py`、`codereview/__main__.py`（库 + CLI，仅标准库）
- 规则说明：`docs/RULES.md`
- 本报告：`docs/RESULTS.md`
- 自测：`tests/test_rules.py`、`tests/corpus/`（含 `expected.json` 标注）
- 数据工具：`tools/gen_benchmark.py`、`tools/run_benchmark.py`、
  `tools/validate_locations.py`
