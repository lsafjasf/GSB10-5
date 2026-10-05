# 缓存键（Cache Key）说明

本库复刻 Docker 经典的**链式层缓存**（legacy layer cache）语义。
"把变化最频繁的那一步放在前面，后面所有层的缓存都白建"正是链式键的必然结果：
每一层的键依赖其父层键，父层一旦分叉，后续层的键全部随之改变。

## 1. 键的计算方式

```
key_0 = SHA256("layercache-v1\n" || ""        || 0x00 || factor(instr_0))
key_i = SHA256("layercache-v1\n" || key_{i-1} || 0x00 || factor(instr_i))
```

- `factor(instr)` 是该指令全部**内容相关输入**的规范化 JSON
  （键名排序、紧凑分隔符、UTF-8 编码），见下表。
- 键输出为 `layercache:` + 64 位十六进制 SHA256 摘要。
- 键是纯函数：相同父链 + 相同输入 → 相同键；任一输入改变 → 键改变。

实现：`layercache/keys.py`（`chain_key` / `instruction_factor`）。

## 2. 哪些因素参与

| 指令 | factor 内容 | 参与项 |
| --- | --- | --- |
| `FROM` | 镜像名 + 基础镜像文件树摘要 | 镜像名、镜像内每个文件的路径/类型/权限/链接目标/内容 SHA256 |
| `RUN` | 规范化后的命令文本 | 命令文本（连续空白压缩为一个空格） |
| `COPY` | 目标路径 + 源条目描述符列表 | 每个源文件的路径、类型、**权限位 mode**、链接目标、大小、内容 SHA256 |
| `ENV` | `K=V` 赋值文本 | 键名与值 |
| `WORKDIR` | 目录路径 | 规范化绝对路径 |
| `LABEL` | 键值文本 | 键名与值 |

隐式参与：**父层键**（链式依赖），因此"顺序"天然参与——
即使两条指令文本完全一样，只要它们在链上的位置不同（父层不同），键就不同。

## 3. 哪些因素明确不参与

- 文件时间戳 `mtime`/`atime`
- 属主 `uid`/`gid`（Docker 内容寻址同样默认忽略，见下）
- inode、构建机绝对路径之外的宿主机信息、缓存生成顺序

`mode`（权限位）**参与**，因为 chmod 会改变文件元数据并可能影响程序行为；
而 `uid/gid` 默认不参与（本模型假设构建对属主不敏感，与 OCI 内容寻址默认策略一致）。
`COPY` 时 `mtime/uid/gid` 仍会被如实写入文件系统，只是不进键、不进内容摘要。

这样做同时满足两个相反的要求：

1. **无关变化不得导致失效**：仅 mtime/uid/gid 变化 → 键与内容摘要都不变 → 命中；
2. **不能因为忽略而错误复用**：内容、权限位、链接目标变化 → 键必然不同 → 重建
   （自测 `NegativeTests` 用双向摘要对比保证这一点）。

## 4. 层内容摘要与镜像摘要

除"键"之外，每个层还有一个**内容摘要**，用于校验"复用的层确实等于重建的层"：

- 层内容 = 该层对文件系统的增删改集合；删除用 `whiteout` 标记。
- 摘要输入同样规范化、按路径排序，且**排除 mtime/uid/gid**：
  普通文件取 `路径 + mode + 大小 + 内容SHA256`；
  目录取 `路径 + mode`；符号链接取 `路径 + target`；删除取 `路径 + whiteout`。
- 镜像摘要 = 应用该层后整棵文件树的树摘要。

实现：`layercache/builder.py`（`changeset_digest`、`tree_digest`），
校验：`layercache/verify.py`。

## 5. 复用判定与"首个分叉层"

1. 先构建基线脚本，所有 miss 层写入共享 `CacheStore`；
2. 再构建候选脚本，逐层出键并查缓存；命中即直接套用缓存变化集（不执行指令）；
3. 第一个 miss 的层为**首个分叉层**；由于链式依赖，它之后的层全部 miss，
   即使指令文本一字不差（自测 `test_mid_change` / `test_reordered` 验证）。

## 6. 具体示例（examples/build_a.dockerfile）

基础镜像 `ubuntu:22.04` 的文件树摘要：

```
578a1916fda37a038bf37e3dc67a8c06...
```

`COPY src/app.py /app/app.py` 的 factor（mode 420 = 0o644，z 为字节数，h 为内容 SHA256）：

```json
{"dest":"/app/app.py","op":"COPY","src":[
  {"h":"03e693d9f2f687e0f40e36a8df7fcb4d1c22974012b7c2a55c000eb30f305824",
   "k":"file","m":420,"p":"/src/app.py","z":15}]}
```

七层实际缓存键：

```
层0 FROM ...     layercache:fd00d0b51453015553b5e2052b9eb1978c035bff5aaa318772a879a43e2c3164
层1 RUN mkdir    layercache:a62154f66f6f35a3e273624a59fc395c3854f7f803f684c00cc2d0f6fad196d2
层2 COPY app.py  layercache:7e0a50b206182488ca3ca9deb32999750a010861dacc8e2312b7428d2f2b908d
层3 COPY ini     layercache:5131d1b07b5dcb021eeb8cb57b6c144a8cbdbadbab9c633ffbed622f3949ba34
层4 RUN v1       layercache:7744f2cb07f08c72ba013b307f6b7e5eaefc788f8dc3c4edd439addf456c1e77
层5 ENV          layercache:764ee2008ac18c490ef4b4b47ed91baf89fd4fdc3e9074abfdd08416a442a28f
层6 WORKDIR      layercache:8f243fd80c33083f4f7032a622138702bea4616bab172245dc028bea4616ba9c2
```

`build_b` 把层 4 的 `v1` 改为 `v2`（同时只改上下文 mtime/uid/gid）：
层 0–3 键完全相同而复用，层 4–6 键全部失效而重建 → 层复用率 4/7 = 57.1%。
