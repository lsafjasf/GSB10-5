# 会话固定（Session Fixation）防护库 — Python 3 标准库

只依赖标准库（`secrets` / `threading`），无任何第三方包。

## 1. 文件清单与运行命令

| 文件 | 作用 |
| --- | --- |
| `session_lib.py` | 防护库本体：登录换号、旧标识即时失效、并发/失败确定语义 |
| `test_session_lib.py` | 自测：首次/连续/并发/失败重试/登出，含标识变化轨迹打印 |
| `randomness_check.py` | 随机性来源说明与 20 万样本统计（唯一性、卡方、汉明距离） |
| `benchmark.py` | 100 万次登录换号操作的耗时与内存峰值基准 |

```bash
cd session_fixation_guard
python3 test_session_lib.py -v      # 自测（5 个用例，含轨迹）
python3 randomness_check.py         # 随机性统计
python3 benchmark.py                # 100 万次基准
```

## 2. 更换规则与时机（核心）

见 `session_lib.py:login()`，全部在同一把 `threading.Lock` 下**原子完成**：

1. **时机**：凭证校验通过**之后**、会话被标记为 `authenticated=True`**之前**。
2. **规则**：
   - 旧标识执行 `del self._sessions[session_id]` —— **立即失效，无任何宽限窗口**；
   - 生成全新标识（与旧标识无派生关系），会话对象整体迁移到新标识下；
   - 新旧标识均有效、可复用的窗口期不存在（删旧与插新在同一临界区内）。
3. **登录失败**（凭证错误）：**不换号**，仅 `failed_attempts += 1` 并抛
   `AuthenticationError`。攻击者预设的标识不会因失败重试获得任何状态。
4. **登出**：标识立即删除。
5. 推荐在 HTTP 层配合：登录响应使用新的 `Set-Cookie` 且带 `HttpOnly`、
   `Secure`、`SameSite` 属性（属于部署配置，不在本库范围）。

## 3. 失效验证数据（旧标识访问被拒绝）

自测实测输出（标识本身每次运行随机，**行为确定**）：

- 首次登录：`validate(旧标识)` → `InvalidSessionError`；新标识校验为
  `authenticated=True, user_id=alice, login_count=1`。
- 连续 5 次登录：前 5 个历史标识逐个 `validate` 全部抛
  `InvalidSessionError`；全程 `active_count == 1`（换号不残留旧会话）。
- 并发登录：8 线程同压一个旧标识，**恰好 1 个成功、7 个收到
  `InvalidSessionError**；旧标识随后访问同样被拒绝。20 轮重复结果一致。
- 失败后成功：3 次失败期间旧标识始终可用；第 4 次成功换号后旧标识立即被拒绝，
  且 `failed_attempts=3` 随数据迁移到新会话。

## 4. 标识变化轨迹（自测实测节选）

**首次登录**
```
匿名会话标识      = 8c755243…
登录成功后新标识  = e463af01…
旧标识 8c755243… 访问被拒绝 (InvalidSessionError)
新标识 e463af01… 校验通过 authenticated=True user=alice
```

**连续 5 次登录（每次必换，单调失效链）**
```
第 0 次（匿名） = ab19171d…
第 1 次登录后   = 046c78cf…
第 2 次登录后   = 688868cf…
第 3 次登录后   = 804c54ab…
第 4 次登录后   = 76372275…
第 5 次登录后   = da811314…
前 5 个历史标识全部访问被拒绝 (InvalidSessionError)，活跃会话始终为 1
```

**并发登录（8 线程同一旧标识，Barrier 同时释放）**
```
旧标识 518867a8… → 胜者 1 个：80850f17…；败者 7 个：InvalidSessionError
旧标识 518867a8… 随后访问：InvalidSessionError
```
确定行为：锁保证串行化，第一个完成换号的线程赢，其余拿到的旧标识已不在表中。

**登录失败后重试**
```
匿名标识          = 12f38817…
3 次失败登录后    = 12f38817…（未换号，failed_attempts=3）
第 4 次成功       = 486dd8e3…（换号）
旧标识 12f38817… 访问被拒绝 (InvalidSessionError)；新标识携带 failed_attempts=3
```

## 5. 随机性来源与样本统计

- **来源链**：`secrets.token_hex(16)` → `os.urandom()` → Linux `getrandom(2)`
  内核 CSPRNG，128 bit 熵；`secrets` 由标准库明确保证适用于安全场景
  （非 `random` 伪随机）。标识空间 2¹²⁸，按生日界碰撞期望需约 2⁶⁴ 次生成。
- `randomness_check.py` 实测（20 万样本）：
  - 唯一性：200,000 个样本，碰撞 **0** 次；
  - 格式：全部为 32 位小写十六进制；
  - 字节均匀性：卡方 χ²=289.6（df=255，正态近似 z=+1.53，|z|<4 通过），
    最少见字节 2,987 次 / 最多 3,285 次（期望 3,125）；
  - 相邻样本平均汉明距离 64.0 / 128 bit（理论期望 64，无相关性迹象）。
  - 样本示例：`ee6857039d690ae673a83f36ee517d99`

## 6. 耗时与内存（100 万次登录会话操作）

`benchmark.py` 实测（Python 3，单线程，单会话反复登录换号 1,000,000 次）：

```
总耗时       0.790 s
平均每次     0.79 µs
吞吐量       约 1,265,000 ops/s
进程峰值 RSS 32.6 MiB (ru_maxrss)
活跃会话数   1（旧标识即时回收，不累积）
```

参考场景——100,000 个并发活跃会话（各建号 + 登录一次）：
耗时 0.223 s，进程峰值 RSS 58.6 MiB。

> 注：数值为本机实测，不同机器会有浮动；结论是每次换号均摊亚微秒级，
> 内存随活跃会话数线性增长、与历史登录次数无关。
