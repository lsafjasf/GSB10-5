"""场景演示：每种操作之后同时打印两个剩余时间（空闲 / 绝对）。"""

from session_expiry import Session

IDLE, ABSOLUTE = 10, 30


def show(tag, s, now):
    ri, ra, expired = s.remaining(now)
    state = "已过期" if expired else "有效"
    print(f"  t={now:>3}  {tag:<14} 剩余空闲={max(ri, 0):>3}s  "
          f"剩余绝对={max(ra, 0):>3}s  -> {state}")


print("场景 1：持续活跃（每 5s 续期一次）")
s = Session(now=0, idle_timeout=IDLE, absolute_timeout=ABSOLUTE)
show("创建", s, 0)
for t in range(5, 30, 5):
    ok = s.touch(t)
    show(f"续期 ok={ok}", s, t)
print("  -- 30s 起：尽管一直活跃，绝对期限已到 --")
for t in (30, 35):
    ok = s.touch(t)
    show(f"续期 ok={ok}", s, t)

print("\n场景 2：长期空闲（创建后不再操作）")
s = Session(now=0, idle_timeout=IDLE, absolute_timeout=ABSOLUTE)
show("创建", s, 0)
for t in (5, 9, 10, 11):
    show("查询", s, t)

print("\n场景 3：边界卡点（now == 过期时刻）")
s = Session(now=0, idle_timeout=IDLE, absolute_timeout=ABSOLUTE)
show("t=9 查询", s, 9)
print(f"  t=10 续期（恰在空闲边界）-> ok={s.touch(10)}  （确定结论：拒绝）")
s2 = Session(now=0, idle_timeout=IDLE, absolute_timeout=ABSOLUTE)
for t in (9, 18, 27):
    s2.touch(t)
show("t=29 查询", s2, 29)
print(f"  t=30 续期（恰在绝对边界）-> ok={s2.touch(30)}  （确定结论：拒绝）")

print("\n场景 4：时钟回拨（t=100 创建，时钟回拨到 90）")
s = Session(now=100, idle_timeout=IDLE, absolute_timeout=ABSOLUTE)
show("创建", s, 100)
ok = s.touch(90)
show(f"续期 ok={ok}", s, 90)
print(f"  last_activity 保持 {s.last_activity}（不回退），空闲窗口未被拉长")
show("t=110 查询", s, 110)
