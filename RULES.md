# docdiff：文档版本结构差异工具

Python 3，仅标准库（`re` `zlib` `difflib` `hashlib` `json` `tracemalloc` `resource` 等）。
输入两份 Markdown 文档（ATX `#` 与 Setext `===`/`---` 标题），输出**结构级**变更记录，
而不是行级 diff：整章搬家只报一条"移动"，子节自动折叠为"随迁"。

## 运行命令

```bash
python3 selftest.py                          # 自测（9 项，覆盖完全相同/仅改名/整章移动/层级调整/增删/组合/还原/阈值边界）
python3 -m docdiff diff v1.md v2.md          # 文本摘要报告
python3 -m docdiff diff v1.md v2.md --json   # JSON 明细（含判定依据）
python3 -m docdiff diff v1.md v2.md --tau-body 0.7 --tau-title 0.8   # 自定义阈值
python3 make_bench.py                        # 生成 10 万行基准文档对 bench/v1.md bench/v2.md
python3 -m docdiff perf --trials 5           # 10 万行性能测量
python3 -m docdiff sweep                     # 阈值影响扫描
python3 -m docdiff report                    # 生成 reports/ 下全部报告（还原验证/阈值/性能）
```

## 处理流程

1. **解析**（`docdiff/parser.py`）：Markdown → 章节树。每节记录：层级、标题、父节、
   行区间、规范化正文（去空行、去首尾空白）。首个标题前的 preamble 不参与比较。
2. **指纹**（`docdiff/fingerprint.py`）：每节正文算 crc32 链式指纹 `body_fp`
   （逐字节相同才相等）和 2-shingle 哈希集合（相邻两行拼接的 crc32 集合）。
3. **配对**（`docdiff/matcher.py`）：跨版本 1:1 匹配，两阶段——
   - 精确配对：`(body_fp, 规范化标题, 层级)` 完全相同，直接配对；
   - 候选配对：shingle 倒排索引 + 同标题召回候选（单 shingle 命中 >200 节视为样板忽略，
     每节最多 30 个候选），按 `score = 0.7*body_jaccard + 0.3*title_sim` 降序贪心接受，
     接受条件 `body_jaccard >= tau_body`；无正文节只能按标题配对
     （`title_sim >= tau_title` 且层级相同）。
4. **归类**（`docdiff/classify.py`）：见下节规则。
5. **摘要**（`docdiff/summary.py`）：明细 → 分组摘要，支持双向还原。

## 归类规则

### 配对节的结构判定（每对配对节计算三个事实）

- `parent_changed`：该节"最近已配对祖先"在新版本中发生变化（跨父移动）；
- `delta_level`：新层级 − 旧层级 ≠ 0（层级调整）；
- `reordered`：同一配对父组内做**加权 LCS**（保序元素最多优先；平局时保序元素
  子树规模之和最小，即优先让"整章"充当移动者），不在保序序列中的元素。

### 主类别（优先级从高到低）

| 类别 | 判定规则 |
|---|---|
| `carried` 随迁 | 是某搬迁根的已配对后代，且层级差与根一致、子树完整（配对后代集合与先序相对顺序一致） |
| `moved` 移动 | `parent_changed` 且 `delta_level == 0`；或同父内 `reordered`（同级换位） |
| `releveled` 层级调整 | `delta_level != 0`（若同时 `parent_changed`，flags 里附带 `moved`） |
| `renamed` 改名 | 配对节规范化标题不同（配对本身由正文证据保证） |
| `modified` 内容修改 | 正文指纹 `body_fp` 不同 |
| `added` / `deleted` | 未配对节，且只记录**未配对子树的根**（子树规模、行数、子节标题入明细） |

结构无变化且标题、正文都未变的配对节不产生记录。一条记录可有多个 flags
（如 `moved`+`renamed` 表示边搬家边改名），主类别用于摘要分组。

### 改名 ≠ 删除+新增 的判定依据

配对只认**正文证据**，与标题、位置无关：
- 仅改名：正文指纹/shingle 不变 → 必然配对成功 → 标题不同即 `renamed`，
  明细中记录 `evidence.title_sim`（标题相似度）与 `evidence.body_fp_equal=true` 作为依据；
- 整章移动：标题、正文都不变 → 精确配对 → 由结构事实（`reordered`/`parent_changed`）
  判为 `moved`，其后代经"子树完整保持"验证折叠为 `carried`；
- 改名+移动：同一配对同时命中两类事实，flags 并集呈现。

自测 `test_rename_vs_move_distinct` 断言：纯改名记录不含 `moved`，纯移动记录不含 `renamed`。

### 阈值（默认值，均可命令行覆盖）

| 阈值 | 默认 | 作用 |
|---|---|---|
| `tau_body` | 0.60 | 正文配对下限：2-shingle Jaccard ≥ 阈值才接受为同一节 |
| `tau_title` | 0.70 | 无正文节配对下限：标题相似度（difflib 序列比）≥ 阈值才接受 |

已知边界（设计取舍）：无正文且标题也大改的节无法配对（无信号），会塌缩为删除+新增；
正文全新但标题不变的节同理。

## 摘要 ↔ 明细 互还原

- `summarize(records)`：明细 → 摘要 `{stats, groups: {类别: [rid...]}}`（确定性：同明细必得同摘要）；
- `expand_summary(summary, records)`：摘要 → 完整明细（按 rid 还原，顺序一致）；
- `collapse_summary(summary)`：摘要 → 明细存根（rid+kind 投影）；
- `verify_roundtrip(records)`：三向验证 + SHA256 摘要。

还原验证数据见 `reports/roundtrip.json`（6 个场景全部三向通过），摘录：

| 场景 | 记录数 | 分组 | 摘要→明细 | 明细→摘要 | 存根投影 |
|---|---|---|---|---|---|
| identical 完全相同 | 0 | - | ✓ | ✓ | ✓ |
| rename_only 仅改名 | 1 | renamed:1 | ✓ | ✓ | ✓ |
| chapter_move 整章移动 | 3 | moved:1 carried:2 | ✓ | ✓ | ✓ |
| relevel 层级调整 | 2 | releveled:2 | ✓ | ✓ | ✓ |
| add_delete 增删 | 2 | added:1 deleted:1 | ✓ | ✓ | ✓ |
| combined 组合 | 3 | moved:1 modified:1 carried:1 | ✓ | ✓ | ✓ |

## 阈值影响数据

完整表格见 `reports/threshold_sweep.md`（`python3 -m docdiff sweep` 重新生成）。

**tau_body（20 行正文分别连续改写 2/4/6 行，实测 Jaccard 0.7273/0.5833/0.4615）：**

| tau_body | 改写2行(0.7273) | 改写4行(0.5833) | 改写6行(0.4615) |
|---|---|---|---|
| 0.40–0.50 | modified | modified | 0.40:modified / 0.50:删+增 |
| 0.60（默认） | modified | 删+增 | 删+增 |
| 0.70 | modified | 删+增 | 删+增 |
| 0.80–0.90 | 删+增 | 删+增 | 删+增 |

**tau_title（无正文节改名，实测标题相似度 0.60/0.75/0.50）：**

| tau_title | 数据库备份→数据库恢复(0.60) | 安装指南→安装指引(0.75) | 安装指南→安装手册(0.50) |
|---|---|---|---|
| 0.50 | renamed | renamed | renamed |
| 0.60 | renamed | renamed | 删+增 |
| 0.70（默认） | 删+增 | renamed | 删+增 |
| 0.80–0.90 | 删+增 | 删+增 | 删+增 |

规律：配对成功 → `modified`/`renamed`；失败 → 塌缩为 `deleted`+`added`。
阈值越高，越多"改写/改名"被当成"删旧增新"。

## 性能数据（10 万行 × 2）

完整数据见 `reports/perf.md`（`python3 -m docdiff perf` 重新生成）。
基准文档：625 章 × 3 节 × 53 行 = 100,000 行（v1/v2 各 2500 节），
v2 含 20 章改名、1 章整章移动、1 章改名+移动、5 节层级调整、30 节内容修改、
5 章删除、5 章新增。

环境：Python 3.12.3 / Linux x86_64（WSL2）/ 16 核。

| 指标 | 数值 |
|---|---|
| 解析 | ~0.07 s |
| 指纹 | ~0.08 s |
| 配对 | ~0.01 s |
| 归类 | ~0.05 s |
| **合计（5 轮最优/中位）** | **0.203 / 0.217 s** |
| 内存峰值（tracemalloc，Python 堆） | 44.1 MB |
| 进程 RSS 峰值 | 163.4 MB |
| 归类结果 | added:5 deleted:5 moved:2 releveled:5 renamed:20 modified:30 carried:6（共 73 条，与设计一致） |

## 文件清单

- `docdiff/` 库源码（parser / fingerprint / matcher / classify / summary / report / benchgen / scenarios）
- `selftest.py` 自测（unittest，9 项）
- `make_bench.py` 基准文档生成
- `reports/roundtrip.json` 还原验证数据
- `reports/threshold_sweep.md` 阈值影响数据
- `reports/perf.md` 耗时与内存数据
- `bench/v1.md` `bench/v2.md` 10 万行基准文档对
