# 回收校验与性能报告

运行环境：Linux，Python 3.12.3，仅标准库。完整原始输出见 `report/selftest_output.txt`。

## 运行命令

```bash
python3 selftest.py            # 全部用例 + 基准（自动建临时根目录）
python3 selftest.py /tmp/mytmp # 指定临时根目录
```

## 用例覆盖与校验数据

| 用例 | 场景 | 校验数据（实测） | 结果 |
|---|---|---|---|
| 1 | 单实例 | 过期 2 个被回收；新文件 1 个跳过（young）；伪造的他实例文件 1 个跳过（foreign）且仍在盘 | PASS |
| 2 | 10 实例并发清理 | 每实例 200 文件（100 过期/100 在用）：回收=1000，越界匹配=0，误删在用=0，耗时 0.03s | PASS |
| 3 | 强杀（SIGKILL）残留 | 见下方残留清单，5 个全部回收，实例目录删除 | PASS |
| 4 | 文件正在被使用 | 持锁期间 reclaimed=0 / skipped_in_use=1 / 文件仍在；释放锁后 reclaimed=1 / 文件已删 | PASS |
| 5 | 5 个回收者并发扫同一批 5000 残留 | 总回收=5000，竞争让位 raced=7464，剩余=0，耗时 0.11s（无重复删除、无异常） | PASS |
| 6 | 基准：10 万文件 / 10 并发实例 | 见下 | PASS |

## 残留清单与回收结果（用例 3，SIGKILL 后）

```
export--victim--192338--000001--49a1b71d.tmp
export--victim--192338--000002--d1fb7ec1.tmp
export--victim--192338--000003--0f550b35.tmp
export--victim--192338--000004--e54a24a4.tmp
export--victim--192338--000005--1acbe45d.tmp
```

识别依据：心跳停更（mtime 超 ttl=1.0s）且 pid 已不存在 → 判定实例死亡。
回收结果：`reclaimed=5, alive_dirs=0, dead_dirs=1`，实例目录随之删除。

## 基准：10 万临时文件 / 10 并发实例

- 规模：10 实例 × 10,000 文件 = 100,000 个临时文件
- 端到端耗时（建文件 + 10 进程并发回收）：**0.78s**
- 单实例 sweep（1 万文件）最大耗时：**0.24s**
- 内存峰值 `ru_maxrss`：单进程最大 **16.1 MiB**，`RUSAGE_CHILDREN` **19.1 MiB**
- 结果：总回收 100,000，残留 0

（耗时为流式扫描 `os.scandir` + 逐文件 `stat`/`flock`，内存与文件数无关，为 O(1)。）
