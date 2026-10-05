# 会话过期与续期：计时规则说明

## 两套独立计时

| 计时 | 起点 | 更新方式 | 过期时刻 |
|---|---|---|---|
| 空闲过期（滑动） | 创建时刻 `created_at` | 每次 `touch` 成功时滑动到操作时刻 `now` | `last_activity + idle_timeout` |
| 绝对过期（固定） | 创建时刻 `created_at` | **永不更新**，续期不影响 | `created_at + absolute_timeout` |

关键性质：绝对期限单独计算，"只要续期就永不过期"不成立——
持续活跃的会话到达绝对期限照样失效。

## 判定规则

- 会话有效 ⟺ 剩余空闲时间 > 0 **且** 剩余绝对时间 > 0，两者必须同时给出。
- 过期判定为闭区间：`remaining <= 0` 即过期，即 `now == expiry` 时刻已过期。
- 续期与过期同一时刻：先判过期再续期，故 `now == expiry` 时的 `touch`
  被确定性地拒绝（返回 `False`），状态不被修改。
- 时钟回拨：`touch` 的 `now < last_activity` 时 `last_activity` 不回退，
  空闲窗口不被拉长；绝对过期时刻任何情况下都不变。

## 运行

```bash
python3 -m unittest -v   # 自测（12 例）
python3 demo.py          # 场景演示，每步打印两个剩余时间
python3 benchmark.py     # 100 万次操作基准
```
