# 分层缓存分析报告

## 复用率数据

| 场景 | 总层数 | 复用 | 重建 | 层复用率 | 字节复用率 | 首个分叉层 | 指令重合度 | 校验 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 完全相同 | 7 | 7 | 0 | 100.0% | 100.0% | - | 100.0% | 通过 |
| 仅顺序调整 | 7 | 2 | 5 | 28.6% | 63.4% | 2 | 100.0% | 通过 |
| 中间一层变化 | 7 | 4 | 3 | 57.1% | 97.0% | 4 | 75.0% | 通过 |
| 仅时间戳与属主变化 | 7 | 7 | 0 | 100.0% | 100.0% | - | 100.0% | 通过 |

## 逐层判定

### 完全相同（base → identical）

| 层 | 指令 | 判定 | 原因 |
| --- | --- | --- | --- |
| 0 | `FROM ubuntu:22.04` | reuse | 缓存键命中，层内容经校验与重建一致 |
| 1 | `RUN mkdir -p app` | reuse | 缓存键命中，层内容经校验与重建一致 |
| 2 | `COPY /src/app.py /app/app.py` | reuse | 缓存键命中，层内容经校验与重建一致 |
| 3 | `COPY /conf/settings.ini /app/settings.ini` | reuse | 缓存键命中，层内容经校验与重建一致 |
| 4 | `RUN mkdir -p data && write data/version.txt v1` | reuse | 缓存键命中，层内容经校验与重建一致 |
| 5 | `ENV APP_HOME=/app` | reuse | 缓存键命中，层内容经校验与重建一致 |
| 6 | `WORKDIR /app` | reuse | 缓存键命中，层内容经校验与重建一致 |

### 仅顺序调整（base → reordered）

| 层 | 指令 | 判定 | 原因 |
| --- | --- | --- | --- |
| 0 | `FROM ubuntu:22.04` | reuse | 缓存键命中，层内容经校验与重建一致 |
| 1 | `RUN mkdir -p app` | reuse | 缓存键命中，层内容经校验与重建一致 |
| 2 | `COPY /conf/settings.ini /app/settings.ini` | rebuild | 首个分叉层：指令或其输入（内容/权限/父链）发生变化 |
| 3 | `COPY /src/app.py /app/app.py` | rebuild | 父层已变化，链式缓存键随之失效 |
| 4 | `RUN mkdir -p data && write data/version.txt v1` | rebuild | 父层已变化，链式缓存键随之失效 |
| 5 | `ENV APP_HOME=/app` | rebuild | 父层已变化，链式缓存键随之失效 |
| 6 | `WORKDIR /app` | rebuild | 父层已变化，链式缓存键随之失效 |

### 中间一层变化（base → mid_change）

| 层 | 指令 | 判定 | 原因 |
| --- | --- | --- | --- |
| 0 | `FROM ubuntu:22.04` | reuse | 缓存键命中，层内容经校验与重建一致 |
| 1 | `RUN mkdir -p app` | reuse | 缓存键命中，层内容经校验与重建一致 |
| 2 | `COPY /src/app.py /app/app.py` | reuse | 缓存键命中，层内容经校验与重建一致 |
| 3 | `COPY /conf/settings.ini /app/settings.ini` | reuse | 缓存键命中，层内容经校验与重建一致 |
| 4 | `RUN mkdir -p data && write data/version.txt v2` | rebuild | 首个分叉层：指令或其输入（内容/权限/父链）发生变化 |
| 5 | `ENV APP_HOME=/app` | rebuild | 父层已变化，链式缓存键随之失效 |
| 6 | `WORKDIR /app` | rebuild | 父层已变化，链式缓存键随之失效 |

### 仅时间戳与属主变化（base → metadata）

| 层 | 指令 | 判定 | 原因 |
| --- | --- | --- | --- |
| 0 | `FROM ubuntu:22.04` | reuse | 缓存键命中，层内容经校验与重建一致 |
| 1 | `RUN mkdir -p app` | reuse | 缓存键命中，层内容经校验与重建一致 |
| 2 | `COPY /src/app.py /app/app.py` | reuse | 缓存键命中，层内容经校验与重建一致 |
| 3 | `COPY /conf/settings.ini /app/settings.ini` | reuse | 缓存键命中，层内容经校验与重建一致 |
| 4 | `RUN mkdir -p data && write data/version.txt v1` | reuse | 缓存键命中，层内容经校验与重建一致 |
| 5 | `ENV APP_HOME=/app` | reuse | 缓存键命中，层内容经校验与重建一致 |
| 6 | `WORKDIR /app` | reuse | 缓存键命中，层内容经校验与重建一致 |

## 复用校验数据

校验方法：候选脚本分别在有缓存与无缓存（逐层重建）下构建，逐层比对层内容摘要与镜像摘要。

| 场景 | 校验层数 | 复用层数 | 层内容一致 | 最终镜像一致 |
| --- | --- | --- | --- | --- |
| 完全相同 | 7 | 7 | 全部一致 | 是 |
| 仅顺序调整 | 7 | 2 | 全部一致 | 是 |
| 中间一层变化 | 7 | 4 | 全部一致 | 是 |
| 仅时间戳与属主变化 | 7 | 7 | 全部一致 | 是 |

## 耗时与内存（1000 组构建脚本）

- 脚本数：1000（共 6000 层）
- 总耗时：0.758 s（平均每组 0.758 ms）
- Python 分配峰值（tracemalloc）：1.01 MiB
- 进程峰值 RSS（ru_maxrss）：19.9 MiB
- 缓存条目数（去重后）：1006
- 层缓存命中率：83.3%（5000 命中 / 1006 未命中）

## 备注

- 负例场景（文件内容/权限位变化）层复用率：文件内容变化（必须失效） 28.6%, 权限位变化（必须失效） 28.6%
- 负例场景同样通过复用正确性校验（复用的前缀层与重建一致）。

