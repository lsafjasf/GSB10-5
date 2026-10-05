# 临时文件命名规则

## 目录与文件名

```
<root>/<instance_id>/<purpose>--<instance_id>--<pid>--<seq:06d>--<token8hex>.tmp
<root>/<instance_id>/heartbeat.json
```

| 组成部分 | 含义 | 约束 |
|---|---|---|
| `root` | 应用级临时根目录，如 `/tmp/gsb1tmp` | 全实例共享 |
| `instance_id` | 实例唯一标识：`<hostname>-p<pid>-<启动时间UTC>-<4hex随机>` | `[A-Za-z0-9._-]`，不含 `--` |
| `purpose` | 用途：`upload` / `cache` / `export` / `job` … | 同上 |
| `pid` | 创建者进程号 | 数字 |
| `seq` | 进程内单调递增序号，6 位前导零 | 数字 |
| `token` | 8 位十六进制随机串 | 防碰撞 |
| `heartbeat.json` | 实例租约：`{instance_id, pid, pid_start, ts}`，心跳线程每 `ttl/3` 重写一次 | 每实例一个 |

唯一性：目录按 `instance_id` 隔离；文件名内嵌 `instance_id + pid + seq + token`，
四元组在单机内不可能碰撞（seq 进程内单调、token 随机、instance_id 含主机名+pid+启动时间+随机）。

## 样例

```
/tmp/gsb1tmp/web01-p4210-20261006T021530Z-a3f9/heartbeat.json
/tmp/gsb1tmp/web01-p4210-20261006T021530Z-a3f9/upload--web01-p4210-20261006T021530Z-a3f9--4210--000017--9c2bd4e1.tmp
/tmp/gsb1tmp/web01-p4210-20261006T021530Z-a3f9/cache--web01-p4210-20261006T021530Z-a3f9--4210--000018--55aa01c3.tmp
```

## 回收规则

1. **sweep()（日常清理）**：只扫本实例目录，且文件名的实例字段必须等于本实例 ID；
   只删 `mtime` 早于 `min_age` 且 flock 可获取的文件。文件名不匹配的一律跳过（`skipped_foreign`）。
2. **reap_orphans()（残留回收）**：扫描 root 下所有实例目录，仅当实例“已死亡”才回收：
   - 死亡判定 = `heartbeat.json` 的 mtime 超过 `heartbeat_ttl`，
     且（pid 不存在，或 `/proc/<pid>` 的启动时间与 heartbeat 记录的 `pid_start` 不一致——防 pid 复用）；
   - 回收前逐文件再做 flock 占用检查与 `min_age` 宽限；
   - 并发回收者抢同一文件时，后到者 `unlink` 遇 `FileNotFoundError` 计入 `raced`，不报错。
3. **正在使用的判定依据（三重，任一命中即跳过）**：
   - **句柄/锁**：创建者持 `flock(LOCK_EX)`，回收方 `LOCK_EX|LOCK_NB` 抢不到 → 在用；
   - **时间**：`mtime` 距今不足 `min_age` → 宽限期，不删；
   - **锁/租约**：实例 `heartbeat.json` 未过期 → 整目录视为活实例，不碰。

> 注意：flock 为建议锁，依赖本库使用者持锁（`new_file()` 自动持锁）；NFS 上 flock 语义受限，建议 root 放本地盘。
