# 口令策略校验库 — 交付说明

纯 Python 3 标准库实现，零第三方依赖。目标是解决“口令被拒但不说为什么”的体验问题：
每条策略独立判定、违反多条全部列出、每条都带人类可读的原因说明。

## 文件

| 文件 | 说明 |
|---|---|
| `password_policy.py` | 库源码：`PasswordPolicy` 配置 + `validate()` 返回 `ValidationReport` |
| `test_password_policy.py` | 18 个自测（unittest），覆盖全部要求的情形 |
| `benchmark.py` | 100 万次校验基准：耗时 + 内存峰值 |
| `demo.py` | 代表性口令的逐条判定明细演示 |

## 运行命令

```bash
cd password_policy
python3 -m unittest -v test_password_policy   # 自测
python3 benchmark.py                          # 100 万次基准
python3 demo.py                               # 逐条判定明细演示
```

## 用法

```python
from password_policy import PasswordPolicy

policy = PasswordPolicy(min_length=8, max_length=16)
report = policy.validate("abc")

report.ok           # False
report.violations   # 全部未通过条目（元组，按可读顺序）
print(report.explain())   # 面向用户的多行明细文本
```

## 长度口径（重要）

**一律按 Unicode 码点数（字符数）判定，即 `len(str)`，不按字节数。**
报告中同时给出 `length_chars`（判定依据）和 `length_bytes_utf8`（仅参考）。

| 口令 | 字符数（判定口径） | UTF-8 字节数 | 判定（min=8） |
|---|---|---|---|
| `密码Ab1!xy` | 8 | 12 | 通过（按字符数） |
| `e + ◌́`（组合序列 U+0065 U+0301）+ `Aa1!wxy` | 9 | 10 | 通过 |
| `é`（预组合 U+00E9）+ `Aa1!wxy` | 8 | 9 | 通过 |

组合序列按码点逐个计数：`e` + 组合重音符 = 2 个字符。若希望按用户感知的
“字”计数，可构造 `PasswordPolicy(normalize=True)`，校验前先做 NFC 规范化，
此时 `e + ◌́` 会被合并为 `é`，按 1 个字符计（有对应自测）。

## 策略条目与判定顺序

逐条独立判定、互不影响（长度失败时其余条目照常检查），违反多条全部列出，
固定按以下可读顺序输出：

1. `min_length` 最小长度（默认 8）
2. `max_length` 最大长度（默认 64）
3. `require_lower` / `require_upper` / `require_digit` / `require_symbol`
4. `no_whitespace` 不得含空白字符
5. `max_repeat` 同一字符连续不超过 N 次（默认 3）
6. `not_common` 不得为常见弱口令（内置小名单，生产可替换）
7. `no_username` 不得包含用户名（可选）

每条结果含 `rule_id`（机器可读）、`title`、`passed`、`message`（如
“当前 7 字符，还差 1 字符”），前端可直接逐条渲染。

## 逐条判定明细（demo.py 实际输出节选）

```
=== 违反多条（短+缺大写+缺数字+缺符号）: 'abc' ===
长度: 3 字符（UTF-8 3 字节，长度按字符数判定）
[未通过] 最小长度 8 字符: 当前 3 字符，还差 5 字符
[通过] 最大长度 16 字符: 当前 3 字符
[通过] 至少 1 个小写字母: 包含小写字母
[未通过] 至少 1 个大写字母: 缺少大写字母（A-Z）
[未通过] 至少 1 个数字: 缺少数字（0-9）
[未通过] 至少 1 个符号: 缺少符号，可用 !"#$%&'()*+,-./:;<=> 等
[通过] 不得包含空白字符: 不含空白字符
[通过] 同一字符连续不超过 3 次: 最长连续重复 1 次
[通过] 不得为常见弱口令: 不在常见弱口令名单中
结论: 4 条策略未通过，请逐条修正。
```

完整输出见 `python3 demo.py`（含全部通过、单条违反、边界、多字节、组合序列共 8 个用例）。

## 边界用例（min=8, max=16，均有自测）

| 用例 | 长度 | 判定 |
|---|---|---|
| `Aa1!wxyz` | 恰好 = min (8) | 全部通过 |
| `Aa1!wxy` | min − 1 (7) | 仅 `min_length` 未通过：“还差 1 字符” |
| `Aa1!wxyzwxyzwxyz` | 恰好 = max (16) | 全部通过 |
| `Aa1!wxyzwxyzwxyzq` | max + 1 (17) | 仅 `max_length` 未通过：“超出 1 字符” |

## 自测覆盖

18 个用例全部通过，覆盖要求的所有情形：

- 全部通过：`test_all_pass`
- 只违反一条：`test_single_violation_only_short`、`test_single_violation_only_missing_digit`
- 违反多条（全部列出、顺序固定、独立判定）：`test_multiple_violations_*`
- 边界长度：`test_boundary_exactly_min_length_passes`、`test_boundary_min_minus_one_fails`、`test_boundary_exactly_max_length_passes`、`test_boundary_max_plus_one_fails`
- 多字节 / 组合序列：`test_multibyte_counts_as_characters_not_bytes`、`test_combining_sequence_*`

## 100 万次校验基准（benchmark.py 实测）

环境：Python 3.12.3，Linux x86-64。样本池混合全通过/单条违反/多条违反/多字节/边界长度。

| 指标 | 结果 |
|---|---|
| 总耗时 | ≈ 5.8 s（两次运行 5.86 s / 5.76 s） |
| 单次平均 | ≈ 5.8 µs |
| 吞吐 | ≈ 170,000 次/秒 |
| Python 堆峰值 | 2.0 KiB（tracemalloc，报告对象即用即弃，无累积） |
| 进程 RSS 峰值 | ≈ 13 MiB（ru_maxrss，含解释器本身） |

结论：单次校验为微秒级、内存为 O（单条口令），放在注册/改密接口的热路径上没有压力。
