# 配置加载规则与交付说明（configkit）

纯 Python 3 标准库实现，无第三方依赖。

## 1. 来源优先级

每层配置项都必须在 schema 中声明（`Field(type, required, default, env)`），
合并时按以下优先级从高到低取**第一个有效值**：

| 优先级 | 层 | 说明 |
|----|----|----|
| 1（最高） | `cli` | 命令行，形如 `--db-port=6000` 或 `--db-port 6000`，连字符转下划线 |
| 2 | `env` | 环境变量，默认名 `APP_<键名大写>`，可在 `Field(env=...)` 自定义 |
| 3 | `file` | JSON 配置文件（顶层为对象） |
| 4（最低） | `default` | schema 中 `Field(default=...)` 声明的默认值 |

## 2. 必填与取值规则

1. **必填声明**：`required=True` 的字段不允许声明 `default`；cli/env/file
   三层均无有效值时报 `missing_required`，结果中该键不存在。
2. **非必填**：三层都没提供有效值时取默认值，来源记为 `default`。
3. **空字符串**（`""` 或仅空白）是"显式空值"，判定为 `empty_value`：
   - 与类型不符严格区分，不会报成类型错误；
   - 该层不参与取值，自动向低一层回退；
   - 即使该层已被更高层覆盖，空串问题仍会报告（防止"静默配错"）。
4. **类型不符**判定为 `type_error`（携带 expected/actual）：
   - env/cli 是字符串，按类型解析（int/float/bool）；bool 接受
     `true/false/1/0/yes/no/on/off`，其余均算类型不符；
   - file 是 JSON 原生类型，直接判定；特别地 JSON `true` 不能填 int 字段；
   - 该层同样不参与取值，向低层回退。
5. **未知键**：任一层出现 schema 未声明的键，记 `unknown_key`，报告所在层、
   键名，并用 `difflib` 给出拼写近似建议（如 `db_hots` → `db_host`）。
6. 校验结果全部收集在 `LoadResult.issues` 中（不静默丢弃），
   `ok` 为 True 当且仅当没有任何问题；需要严格模式可用 `load_or_raise()`。

## 3. 生效来源数据

`LoadResult` 提供：
- `values`：生效值；`sources`：每个键的生效层（cli/env/file/default）；
- `overridden`：覆盖链，即被哪些层覆盖（含"值无效但确实出现过"的层）；
- `issues`：全部问题；`unknown_keys()`：未知键清单。

## 4. 未知键清单（demo 实际输出）

```
[env]  dbug     -> 是否想写: debug
[file] db_hots  -> 是否想写: db_host, db_port
[file] timeout  （无近似建议）
```

## 5. 性能（10 万次加载）

环境：Python 3.12.3，5 个键的 schema，cli/env/file 三层均有数据。

| 指标 | 数值 |
|----|----|
| 总耗时 | 12.6 s |
| 单次平均 | ≈ 126 µs |
| 吞吐量 | ≈ 7,900 次/s |
| tracemalloc 增量峰值 | 25 KiB（逐次释放，无累积泄漏） |
| 进程 RSS 峰值 | 31.3 MiB（含解释器） |

## 6. 运行命令

```bash
cd src
python3 -m unittest -v   # 16 个自测：默认/覆盖/未知键/必填缺失/空串vs类型
python3 demo.py          # 打印生效来源、未知键清单、空串问题
python3 bench.py 100000  # 耗时与内存峰值（次数可选）
```
