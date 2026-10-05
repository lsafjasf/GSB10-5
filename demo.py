from session_expiry import ManualClock, SessionManager, SessionSnapshot


def show(label: str, snapshot: SessionSnapshot) -> None:
    print(
        f"{label:<24} "
        f"valid={str(snapshot.valid):<5} "
        f"reason={snapshot.reason:<17} "
        f"idle_remaining={snapshot.idle_remaining:>5.1f}s "
        f"absolute_remaining={snapshot.absolute_remaining:>5.1f}s"
    )


def continuous_activity() -> None:
    print("\n[持续活跃：绝对期限仍生效]")
    clock = ManualClock()
    manager = SessionManager(idle_timeout=10, absolute_timeout=30, clock=clock)
    show("create t=0", manager.create("active"))
    for tick in (9, 18, 27):
        clock.set(tick)
        show(f"touch t={tick}", manager.touch("active"))
    clock.set(30.5)
    show("touch t=30.5", manager.touch("active"))


def long_idle() -> None:
    print("\n[长期空闲：空闲期限生效]")
    clock = ManualClock()
    manager = SessionManager(idle_timeout=10, absolute_timeout=30, clock=clock)
    show("create t=0", manager.create("idle"))
    clock.advance(11)
    show("touch t=11", manager.touch("idle"))


def boundaries() -> None:
    print("\n[边界：到达截止时刻即过期]")
    clock = ManualClock()
    idle_manager = SessionManager(idle_timeout=10, absolute_timeout=30, clock=clock)
    show("create t=0", idle_manager.create("idle-boundary"))
    clock.set(10)
    show("touch idle t=10", idle_manager.touch("idle-boundary"))

    clock = ManualClock()
    absolute_manager = SessionManager(idle_timeout=100, absolute_timeout=50, clock=clock)
    show("create t=0", absolute_manager.create("absolute-boundary"))
    clock.set(50)
    show("touch absolute t=50", absolute_manager.touch("absolute-boundary"))

    clock = ManualClock()
    both_manager = SessionManager(idle_timeout=10, absolute_timeout=10, clock=clock)
    show("create t=0", both_manager.create("both-boundary"))
    clock.set(10)
    show("touch both t=10", both_manager.touch("both-boundary"))


def clock_rollback() -> None:
    print("\n[时钟回拨：剩余时间不增加]")
    clock = ManualClock(100)
    manager = SessionManager(idle_timeout=10, absolute_timeout=30, clock=clock)
    show("create t=100", manager.create("rollback"))
    clock.set(109)
    show("touch t=109", manager.touch("rollback"))
    clock.set(105)
    show("get t=105(raw)", manager.get("rollback"))
    clock.set(119)
    show("get t=119(raw)", manager.get("rollback"))


if __name__ == "__main__":
    continuous_activity()
    long_idle()
    boundaries()
    clock_rollback()
