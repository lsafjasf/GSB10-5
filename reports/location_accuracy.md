# 定位校验数据

校验方式：`fixtures/manifest.json` 是**手工标注**的期望清单
(文件, 规则, 行号, 所在结构)。引擎实际报警与之做四元组精确比对。
期望报警 19 条，精确命中 19 条。
**定位精度 = 100.0%** (行号与结构均完全一致)。

| 文件 | 样本类型 | 期望 | 精确命中 | 漏 | 多余 |
|---|---|---|---|---|---|
| `crossfile/main.py` | 跨文件上下文项目 | 2 | 2 | 0 | 0 |
| `positive/p1_basics.py` | 真实问题样本 | 11 | 11 | 0 | 0 |
| `positive/p2_nested.py` | 真实问题样本 | 4 | 4 | 0 | 0 |
| `positive/p3_usage_in_comment.py` | 真实问题样本 | 2 | 2 | 0 | 0 |

## 逐条定位明细 (实际报警)

```
crossfile/main.py:4:1: RV009 [module] 从模块 'helper' 导入未定义的名字 'compute_totals'
crossfile/main.py:12:12: RV009 [function:bad_call] 模块 'helper' 未定义顶层名字 'compute_totals'
positive/p1_basics.py:3:1: RV003 [module] 导入的名字 'sys' 未在代码中使用
positive/p1_basics.py:6:34: RV001 [function:add_item] 函数 'add_item' 使用可变默认参数
positive/p1_basics.py:12:23: RV001 [function:configure] 函数 'configure' 使用可变默认参数
positive/p1_basics.py:16:18: RV001 [function:parse] 函数 'parse' 使用可变默认参数
positive/p1_basics.py:23:5: RV002 [function:divide] 裸 except 捕获所有异常
positive/p1_basics.py:28:8: RV004 [function:shallow_equal] 与单例 None 做相等比较，应使用 is/is not
positive/p1_basics.py:30:8: RV004 [function:shallow_equal] 与单例 True 做相等比较，应使用 is/is not
positive/p1_basics.py:39:5: RV005 [function:cleanup] 异常被 pass 静默吞掉
positive/p1_basics.py:45:5: RV006 [function:early_return] 该语句位于 return/raise/continue/break 之后，不可达
positive/p1_basics.py:48:1: RV007 [function:version_one] 函数 'version_one' 在同作用域被重复定义，较早的定义被覆盖
positive/p1_basics.py:57:12: RV008 [function:run_user_code] 调用内建 eval() 执行动态代码
positive/p2_nested.py:5:30: RV001 [method:Service.__init__] 函数 '__init__' 使用可变默认参数
positive/p2_nested.py:9:25: RV001 [nested:Service.rebuild.<locals>.inner] 函数 'inner' 使用可变默认参数
positive/p2_nested.py:16:9: RV005 [method:Service.find] 异常被 pass 静默吞掉
positive/p2_nested.py:21:9: RV006 [method:Service.stop] 该语句位于 return/raise/continue/break 之后，不可达
positive/p3_usage_in_comment.py:1:1: RV003 [module] 导入的名字 'sys' 未在代码中使用
positive/p3_usage_in_comment.py:2:1: RV003 [module] 导入的名字 'tempfile' 未在代码中使用
```

行号可直接用 `cat -n <文件>` 与手工清单逐条核对；
`tests/test_fixtures.py` 在 CI 中强制要求定位精度 100%。
