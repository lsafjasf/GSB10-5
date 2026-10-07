# 进程信号转发与清理：实现、自测与数据报告

Python 3，仅标准库。所有宽限/超时逻辑的时间均由注入的时钟决定
（生产用单调实时钟，自测用 `FakeClock` 推进逻辑时间，零真实等待）。

## 一、文件清单与运行命令

| 文件 | 说明 |
| --- | --- |
| `procguard.py` | 库本体：独立会话启动、信号转发、TERM→KILL 分级清理、重入保护、存活校验 |
| `child_worker.py` | 测试用子进程：驻留 / 多层派生 / 忽略温和信号 |
| `test_procguard.py` | 7 个自测：3 个注入时钟策略单测 + 4 个真实进程集成测试 |
| `bench.py` | N 次启动/清理基准（耗时分布 + 内存峰值 + 双重零遗留校验） |
| `demo.py` | 端到端演示：外部信号 → supervisor 转发 → 整组清理 |

```bash
cd procguard

# 自测（单测 + 四类真实场景集成测试）
python3 -m unittest test_procguard -v

# 1000 次启动/清理基准
python3 bench.py 1000            # 计时
python3 bench.py 1000 --memory   # Python 堆峰值（tracemalloc）

# 端到端演示
python3 demo.py /tmp/demo.pids & kill -TERM $!
```

环境：Python 3.12.3 / Linux 6.18（WSL2，/proc 可见，本机约 3.4k 进程）。

## 二、信号转发范围（进程组 / 会话）与多层覆盖原理

**启动隔离。** `ProcessTree.start()` 以 `start_new_session=True`
（等价于 `setsid(2)`）启动被监管子进程，子进程因此：

1. 成为一个全新**会话(session)**的会话首进程，与 supervisor 的控制终端脱离；
2. 同时成为一个全新**进程组(process group)**的组长，`pgid == 子进程 pid`。

**转发 = 进程组广播。** supervisor 收到 SIGTERM/SIGINT/SIGHUP/SIGQUIT/
SIGUSR1/SIGUSR2（可配置，SIGKILL/SIGSTOP 不可捕获故不转发）后调用
`os.killpg(pgid, signum)`，一次系统调用投递到组内**每一个**进程。

**为什么覆盖任意派生层数。** 子进程 fork/exec 出来的子孙默认**继承父进程的
pgid 和 sid**；进程组关系不会因普通 fork/exec 改变，只有子孙自己显式调用
`setsid()`/`setpgid()`（典型是双 fork 守护进程）才会离开本组。因此多层派生
（测试覆盖 3 层 13 进程、7 进程两组）无需逐层遍历进程树，整组一次信号即覆盖。

**已知边界（有意排除）。** 显式 `setsid()` 逃逸为独立会话的守护进程不在本
组内，`killpg` 无法覆盖；这类进程需要其他监管机制（pidfile、cgroup 等）。

**回收保证。** supervisor 通过 `prctl(PR_SET_CHILD_SUBREAPER)` 把自己注册为
child subreaper（失败则静默降级）：子孙的直接父进程死后，它们会被过继给
supervisor 而不是 PID 1，于是 supervisor 能用 `waitpid(-1, WNOHANG)` 回收全部
僵尸，不留下“死了但还挂在 /proc 里等 init 回收”的窗口。

## 三、清理算法（procguard.py）

1. 进入清理前置 `_cleaning` 标志；清理途中再来信号只登记到
   `signals_during_cleanup` 并立即返回，并发的 `cleanup()` 调用直接返回
   “进行中”结果——清理流程不重启、不中断。
2. **温和阶段**：`killpg(pgid, SIGTERM)`（或实际触发信号），用注入时钟控制
   的宽限期轮询。轮询用 O(1) 组存活探针 `killpg(pgid, 0)`
   （信号 0 不投递，只检查组内是否还有进程，含僵尸）。
3. **强制兜底**：宽限期结束组仍在 → `killpg(pgid, SIGKILL)`，再等一个
   kill 宽限期。SIGKILL 不可被忽略/屏蔽，忽略温和信号的进程必被杀死。
4. **回收**：`waitpid(-1, WNOHANG)` 循环回收直接子进程及过继子孙。
5. **校验**：探针确认组消失；非空时才做一次全量 `/proc/*/stat` 扫描列出
   存活明细（生产热路径上避免每次轮询都扫 3.4k 个 /proc 项）。

## 四、自测结果（7/7 通过，`python3 -m unittest test_procguard -v`）

注入时钟（`FakeClock`）策略单测：

- `test_term_succeeds_no_escalation`：温和信号即退出，不升级 KILL，逻辑耗时 0。
- `test_escalate_to_kill_exactly_at_timeout`：进程不退，逻辑时间恰在 0.5s
  宽限点升级 SIGKILL，TERM/KILL 各恰好一次。
- `test_reentrant_signal_during_cleanup_is_recorded`：清理途中注入第二个信号
  并并发调用 `cleanup()`——信号被登记、并发调用被拒绝，TERM/KILL 仍各一次。

真实进程集成测试（本次运行实测数据）：

| 场景 | 进程数 | 结果 | TERM 阶段 | KILL 阶段 | 总用时 | 升级 KILL |
| --- | --- | --- | --- | --- | --- | --- |
| 单进程 `single` | 1 | completed | 0.010 s | — | 0.010 s | 否 |
| 多层派生 `multilevel`（depth=2, fanout=3） | 13（3 层） | completed | 0.010 s | — | 0.010 s | 否 |
| 子进程忽略温和信号 `ignore` | 2（父子均 SIG_IGN） | completed | 0.300 s（=宽限） | 0.020 s | 0.321 s | **是** |
| 清理中再次收到信号 `signal-during-cleanup` | 2（SIG_IGN，0.15s 后注入 SIGINT） | completed | 0.800 s | 0.020 s | 0.820 s | **是** |

“清理中再次收到信号”场景结果：`signals_during_cleanup=[2]`（SIGINT 被登记），
清理按原计划走完 TERM 宽限 → SIGKILL 兜底，`completed=True`，零遗留。

多层派生清理前组内清单（一次实测）：
`55763,55765,55766,55767,55769,55770,55771,55772,55773,55774,55775,55776,55777`
（13 个，pgid 均为 55763）；清理后存活清单：`[]`。

端到端演示（`demo.py`，depth=2 fanout=2 → 7 进程）实测：

```
supervisor pid=121297, 子进程组 pgid=121300（子进程为会话首进程/组长）
转发信号:          SIGTERM 经 killpg(121300) 投递到整组
清理结果:          pgid=121300 completed=True escalated=False total=0.000s survivors=[-]
启动的全部 pid:    [121300, 121301, 121302, 121303, 121304, 121305, 121306]
清理后存活清单:    无
/proc 组内扫描:    空
supervisor 退出码: 0
```

## 五、1000 次启动/清理基准（`bench.py`，被启动进程 `/bin/sleep 60`）

计时模式（无 tracemalloc 开销）：

| 指标 | 值 |
| --- | --- |
| 迭代次数 | 1000 |
| 总耗时 | **5.433 s**（含 1000 次进程启动 + 清理） |
| 吞吐 | 184.0 次/s |
| 单次清理 mean / p50 / p95 / p99 | 5.144 / 5.130 / 5.192 / 5.249 ms |
| 单次清理 min / max | 5.082 / 7.676 ms |
| SIGKILL 兜底次数 | 0 |
| 进程 RSS 峰值 | **15.80 MB**（`ru_maxrss`） |

内存模式（`python3 bench.py 1000 --memory`）：

| 指标 | 值 |
| --- | --- |
| Python 堆峰值（tracemalloc） | **0.28 MB** |
| 进程 RSS 峰值 | 16.09 MB |
| 总耗时 / mean 单次 | 5.571 s / 5.162 ms |

**零遗留校验（两种方式交叉核对，基准内置）：** 对 1000 个 pgid 逐一做
`killpg(pgid, 0)` 探针 → 全部不存在；再做一次全量 `/proc/*/stat` 扫描交叉
核对 → 无任何 pgid 命中。结论：**1000/1000 全部清理干净**。基准结束后另用
`ps` 检查，无 `sleep`/`child_worker` 残留，无过继到 PID 1 的僵尸。

说明：单次清理 ~5ms 主要是本机环境开销（fork/exec、信号投递、回收与调度
延迟）；早期版本轮询时全量扫描 /proc（本机 3.4k 项，单次扫描 50ms，开
tracemalloc 时 125ms），已改为 O(1) 组探针，仅在最终需要列出存活明细时扫描。
