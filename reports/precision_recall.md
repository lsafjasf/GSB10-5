# 误报与漏报数据

## 测试用例构造方式

- `fixtures/positive/`：3 个文件，手工植入全部 9 类规则的真实
  问题共 17 条，
  每条在 `manifest.json` 中标注 (规则, 行号, 结构)。同一文件刻意混入
  正确代码 (如 `if default is None`、`except KeyError: return None`)，
  防止规则“逢关键字必报”。
- `fixtures/negative/n1_strings_and_comments.py`：所有问题形态
  (可变默认参数、裸 except、eval、== None、不可达代码、重复 def、
  未用导入) 都只出现在**注释、普通字符串、三引号字符串**里。
- `fixtures/negative/n2_clean.py`：结构正常的干净代码，验证正确
  写法不被误伤。
- `fixtures/positive/p3_usage_in_comment.py`：名字 `sys` 只在注释、
  `tempfile` 只在字符串里出现，验证“使用计数”只看 `ast.Name` 节点，
  不看文本。
- `fixtures/crossfile/`：两文件小项目。`helper.py` 定义 2 个函数；
  `main.py` 含 1 处正确的 `from ... import`、1 处错误的、1 处正确的
  属性访问、1 处错误的属性访问，以及 1 处对已导入名字的正确使用。

## 判定口径

- TP：实际报警四元组 (文件,规则,行,结构) 与手工清单完全一致。
- FP：报警不在清单中 (含 negative 样本里的任何报警)。
- FN：清单条目未被精确命中。

TP=19  FP=0  FN=0
**精确率 P=1.0000    召回率 R=1.0000**

| 规则 | TP | FP | FN |
|---|---|---|---|
| RV001 | 5 | 0 | 0 |
| RV002 | 1 | 0 | 0 |
| RV003 | 3 | 0 | 0 |
| RV004 | 2 | 0 | 0 |
| RV005 | 2 | 0 | 0 |
| RV006 | 2 | 0 | 0 |
| RV007 | 1 | 0 | 0 |
| RV008 | 1 | 0 | 0 |
| RV009 | 2 | 0 | 0 |

## 字符串 / 注释假问题专项结果

- `n1_strings_and_comments.py` (34 行) 包含 8 处假问题
  (4 处注释 + 三引号代码片段 4 类 + 拼接字符串 + 模板字符串)：
  **实际报警 0 条**。
- `n2_clean.py` 干净代码：**实际报警 0 条**。
- 反向验证：同样的名字若只出现在注释/字符串里，RV003 仍应报
  `p3_usage_in_comment.py`，实际命中 2/2 条 (第 1、2 行)。

## 跨文件专项结果

- 错误的 `from helper import compute_totals` 与属性访问
  `helper.compute_totals(...)` 均被 RV009 命中 (main.py 第 4、12 行)。
- 同文件中的 4 处正确跨文件用法均未误报。

## 大规模漏报复核

见 `reports/performance.md`：10 万行合成项目中按固定种子植入的
30 个真实问题必须全部检出，数量不一致则基准脚本返回非零退出码。
