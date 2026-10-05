# 复数形式选择自测报告（2026-10-05 11:09:21）

## 1. 边界取值类别表（基数）

| 输入 | en | ru | ar | fr | ja |
|---|---|---|---|---|---|
| `0` | other | many | zero | one | other |
| `1` | one | one | one | one | other |
| `2` | other | few | two | other | other |
| `0.5` | other | other | other | one | other |
| `1.0` | other | other | one | one | other |
| `1.00` | other | other | one | one | other |
| `1.5` | other | other | other | one | other |
| `2.0` | other | other | two | other | other |
| `2.5` | other | other | other | other | other |
| `-1` | one | one | one | one | other |
| `-2` | other | few | two | other | other |
| `-0.5` | other | other | other | one | other |
| `-1.5` | other | other | other | one | other |
| `11` | other | many | many | other | other |
| `12` | other | many | many | other | other |
| `14` | other | many | many | other | other |
| `21` | other | one | many | other | other |
| `22` | other | few | many | other | other |
| `25` | other | many | many | other | other |
| `101` | other | one | other | other | other |
| `111` | other | many | many | other | other |
| `1000000` | other | many | other | many | other |
| `1000000000000000000000000000000` | other | many | other | many | other |
| `123456789012345678901234567890` | other | many | many | other | other |

说明：负数按 CLDR 规定取绝对值后判定；`1.0`/`1.00` 以字符串传入，保留可见小数位（v=1 / v=2），与 `1`（v=0）类别可能不同。

### 序数（ordinal）边界表

| 输入 | en | en 后缀 | fr |
|---|---|---|---|
| 0 | other | 0th | other |
| 1 | one | 1st | one |
| 2 | two | 2nd | other |
| 3 | few | 3rd | other |
| 4 | other | 4th | other |
| 11 | other | 11th | other |
| 12 | other | 12th | other |
| 13 | other | 13th | other |
| 21 | one | 21st | other |
| 22 | two | 22nd | other |
| 23 | few | 23rd | other |
| 101 | one | 101st | other |
| 102 | two | 102nd | other |
| 103 | few | 103rd | other |
| 111 | other | 111th | other |

## 2. 数字格式化与类别判定联动对照

同一数值、不同格式化参数 → 不同类别（类别取自格式化后的串）：

| 语言 | 输入 | decimals | 格式化结果 | 类别 | 说明 |
|---|---|---|---|---|---|
| en | `1` | — | `1` | **one** | 1 位整数 |
| en | `1` | 1 | `1.0` | **other** | 保留 1 位小数 → v=1 |
| en | `1` | 2 | `1.00` | **other** | 保留 2 位小数 → v=2 |
| ru | `1` | — | `1` | **one** | v=0 → one |
| ru | `1` | 1 | `1.0` | **other** | v=1 → other |
| ru | `1.04` | 1 | `1.0` | **other** | 四舍五入为 1.0 → other |
| ru | `1.04` | 0 | `1` | **one** | 四舍五入为 1 → one |
| ar | `1` | — | `1` | **one** | n=1 → one |
| ar | `1` | 1 | `1.0` | **one** | n=1（ar 用 n 判定）→ one |
| ar | `1` | 2 | `1.00` | **one** | n=1 → one |
| fr | `1000000` | — | `1,000,000` | **many** | i%10^6=0 且 v=0 → many |
| fr | `1000000` | 1 | `1,000,000.0` | **other** | v=1 → other |
| fr | `1500000` | — | `1,500,000` | **other** | i%10^6≠0 → other |
| en | `1234567.891` | 2 | `1,234,567.89` | **other** | 千分位 1,234,567.89 |
| ru | `1234567.891` | 2 | `1,234,567.89` | **other** | 千分位不影响操作数 |

序数联动（en 后缀选择同样走类别判定）：

| 输入 | 类别 | 渲染 |
|---|---|---|
| 1 | one | 1st |
| 2 | two | 2nd |
| 3 | few | 3rd |
| 4 | other | 4th |
| 11 | other | 11th |
| 12 | other | 12th |
| 13 | other | 13th |
| 21 | one | 21st |
| 22 | two | 22nd |
| 23 | few | 23rd |
| 101 | one | 101st |
| 111 | other | 111th |

## 3. 对拍 A：CLDR 官方样本逐条对拍

数据源：CLDR 48 `plurals.json` / `ordinals.json` 中每条规则携带的 `@integer` / `@decimal` 样本（区间样本取端点+中点，紧凑记数法样本如 `1c6` 跳过）。

| 语言 | 类型 | 样本数 | 通过 | 失败 |
|---|---|---|---|---|
| en | cardinal | 31 | 31 | 0 |
| en | ordinal | 51 | 51 | 0 |
| ru | cardinal | 58 | 58 | 0 |
| ru | ordinal | 21 | 21 | 0 |
| ar | cardinal | 102 | 102 | 0 |
| ar | ordinal | 21 | 21 | 0 |
| fr | cardinal | 35 | 35 | 0 |
| fr | ordinal | 22 | 22 | 0 |
| ja | cardinal | 30 | 30 | 0 |
| ja | ordinal | 21 | 21 | 0 |

合计 392 条样本，失败 0 条。✅ 全部通过

## 4. 对拍 B：手写规则 vs CLDR 规则文本解析器（密集网格）

同一批输入分别用手写规则（plural.py）和独立解析 CLDR 规则文本（cldr_parser.py 直接解释 cldr_data.py 中的官方规则串）判定，逐条比对。

| 语言 | 类型 | 比对数 | 不一致 |
|---|---|---|---|
| en | cardinal | 2989 | 0 |
| en | ordinal | 2989 | 0 |
| ru | cardinal | 2989 | 0 |
| ru | ordinal | 2989 | 0 |
| ar | cardinal | 2989 | 0 |
| ar | ordinal | 2989 | 0 |
| fr | cardinal | 2989 | 0 |
| fr | ordinal | 2989 | 0 |
| ja | cardinal | 2989 | 0 |
| ja | ordinal | 2989 | 0 |

合计比对 29890 次，不一致 0 次。✅ 两套实现完全一致

## 5. 耗时：100 万个（数字 × 语言）组合

- 环境：Python 3.12.3 / x86_64 / Linux
- 组合构成：整数 40%、小数 30%、负数 15%、零/一/二 7.5%、10^20~10^30 极大数 7.5%，5 种语言均匀分布
- 总耗时：**3.804 秒**（含格式化 + 操作数计算 + 类别判定）
- 吞吐：262,904 次/秒，平均 3.80 µs/次
- 类别分布：{'few': 54608, 'many': 241539, 'one': 54511, 'other': 632115, 'two': 8578, 'zero': 8649}

RESULT: PASS
