# 插件版本兼容性检查（plugin_compat）

纯 Python 3 标准库实现。核心观点：**版本号涨了不代表接口不兼容，语义上改了一个默认值才真要命。**
因此判定不读取版本号，只比较两个版本的**接口清单（manifest）**：参数、返回值、默认值、错误语义。

## 文件

- `compat_check.py` — 库：`check_compatibility(old, new) -> CheckResult`
- `test_compat_check.py` — 自测（`unittest`，20 个用例）
- `demo.py` — 打印兼容性判定表与不兼容清单
- `bench.py` — 1000 对版本的耗时与内存基准

## 判定依据（规则表）

接口清单把每个公开函数描述为：参数列表（名字、调用种类 kind、是否必填、默认值）、
返回值契约、错误语义（`{触发条件: 异常名}`）。逐条规则如下，全部规则见
`compat_check.py` 中的 `RULES` 字典（机器可读、含理由）。

| 规则代码 | 变化 | 判定 | 依据 |
|---|---|---|---|
| `FUNCTION_REMOVED` | 删除公开函数 | 不兼容 | 现有调用方 import/调用直接失败 |
| `FUNCTION_ADDED` | 新增公开函数 | 向后兼容 | 不影响任何现有调用方 |
| `PARAM_REMOVED` | 删除参数 | 不兼容 | 按位置或按名传参的旧调用抛错 |
| `PARAM_ADDED_REQUIRED` | 新增必填参数 | 不兼容 | 省略该参数的旧调用失败 |
| `PARAM_ADDED_OPTIONAL` | 新增可选参数 | 向后兼容 | 旧调用点不受影响 |
| `PARAM_KIND_CHANGED` | 参数调用种类改变（如位置→仅关键字） | 不兼容 | 原本合法的调用形式无法绑定 |
| `REQUIRED_FLAG_CHANGED` | 可选变必填 | 不兼容 | 省略该参数的调用点失败（必填变可选为向后兼容） |
| `DEFAULT_CHANGED` | **仅默认值改变** | **不兼容** | 省略参数的调用方**无报错地**得到不同行为，是最危险的静默语义变化 |
| `RETURN_TYPE_CHANGED` | 返回值契约改变 | 不兼容 | 消费旧返回结构的调用方出错 |
| `ERROR_REMOVED` | 某条件不再抛出原错误 | 不兼容 | 依赖 `except` 该错误做分支的调用方行为错乱 |
| `ERROR_ADDED` | 新增会抛错的条件 | 不兼容 | 旧调用路径上出现了调用方未捕获的异常 |
| `ERROR_NAME_CHANGED` | **同一条件改抛别的异常** | **不兼容** | `except OldError` 不再匹配，错误处理失效 |

总判定优先级：任一"不兼容"发现 → `incompatible`（不兼容）；
否则任一"向后兼容"发现 → `backward_compatible`（向后兼容）；
否则 → `compatible`（兼容，接口完全一致）。

## 兼容性判定表（`python3 demo.py` 实测输出）

| 样例 | 判定 |
|---|---|
| 完全相同（仅版本号 1.0.0 → 2.0.0） | 兼容 |
| 新增可选参数 `quality=90` | 向后兼容 |
| 仅改默认值 `width: 800 → 1024` | **不兼容** |
| 仅改错误语义 `bad_dimension: ValueError → TypeError` | **不兼容** |
| 删除错误语义 `file_missing` | 不兼容 |
| 删除函数 `save_image` | 不兼容 |
| 新增必填参数 `mode` | 不兼容 |
| 修改返回值 `Image → bytes` | 不兼容 |

## 不兼容清单（仅改默认值/错误语义的样例）

`demo.py` 实测输出（完整清单含全部不兼容样例）：

```
- 仅改默认值 width: 800 -> 1024: DEFAULT_CHANGED @ resize_image(width)
- 仅改错误语义 bad_dimension: ValueError -> TypeError: ERROR_NAME_CHANGED @ resize_image errors[bad_dimension]
- 删除错误语义 file_missing: ERROR_REMOVED @ resize_image errors[file_missing]
- 删除函数 save_image: FUNCTION_REMOVED @ function save_image
- 新增必填参数 mode: PARAM_ADDED_REQUIRED @ resize_image(mode)
- 修改返回值 Image -> bytes: RETURN_TYPE_CHANGED @ function resize_image
```

结果同时提供机器可读形式（`CheckResult.as_dict()`），`incompatibilities`
字段即不兼容清单，每条含 `code / target / detail`。

## 可复现性

- 判定只依赖输入清单，无随机数、无时间戳、无环境依赖；
- 函数名、参数差集、错误条件均按排序顺序遍历，发现列表顺序稳定；
- 自测 `TestDeterminism` 验证：同一对版本重复检查 50 次结果完全一致，
  且 manifest 字典键顺序不影响结果。

## 性能（1000 对版本，`python3 bench.py`）

环境：Python 3.12.3，Linux x86-64。3 次运行取范围：

| 指标 | 数值 |
|---|---|
| 总耗时 | 53–60 ms |
| 平均每对 | ~53–60 µs |
| 内存峰值（tracemalloc） | 17.3 KiB |
| 进程最大 RSS | ~19 MiB |

基准中 1000 对均匀覆盖 5 类变化（完全相同/新增可选参数/改默认值/
改错误语义/新增函数），判定分布：兼容 200、向后兼容 400、不兼容 400。

## 运行命令

```bash
cd plugin_compat
python3 -m unittest test_compat_check -v   # 自测（20 个用例）
python3 demo.py                            # 判定表 + 不兼容清单
python3 bench.py                           # 1000 对耗时与内存峰值
```
