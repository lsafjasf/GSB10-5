"""session_guard 自测：场景验证 + 随机性统计 + 100 万次操作基准。

运行：python3 selftest.py
"""

import math
import resource
import threading
import time
import tracemalloc
from collections import Counter

from session_guard import (
    AuthenticationError,
    InvalidSessionError,
    SessionStore,
)

USERS = {"alice": "s3cret-pass"}


def authenticator(username: str, password: str) -> bool:
    return USERS.get(username) == password


def short(sid: str) -> str:
    return sid[:12] + "…"


def hr(title: str) -> None:
    print("\n" + "=" * 68)
    print(title)
    print("=" * 68)


# ---------------------------------------------------------------- 场景 1
def test_first_login() -> None:
    hr("场景 1：首次登录 —— 登录成功必须更换标识")
    store = SessionStore()
    sid0 = store.create_session()
    print(f"登录前（匿名/可能被预设）标识: {short(sid0)}")
    sid1 = store.login(sid0, "alice", "s3cret-pass", authenticator)
    print(f"登录成功后新标识           : {short(sid1)}")
    assert sid1 != sid0, "登录后标识必须改变"
    s = store.get(sid1)
    assert s.authenticated and s.username == "alice" and s.generation == 1
    print(f"新会话状态: authenticated={s.authenticated} user={s.username} "
          f"generation={s.generation}")
    print("PASS: 登录成功后标识已更换")


# ---------------------------------------------------------------- 场景 2
def test_old_id_invalidated() -> None:
    hr("场景 2：旧标识在更换后立即失效 —— 旧标识访问被拒绝")
    store = SessionStore()
    sid0 = store.create_session()
    sid1 = store.login(sid0, "alice", "s3cret-pass", authenticator)
    print(f"旧标识: {short(sid0)}   新标识: {short(sid1)}")

    rejected = 0
    for attempt in range(1, 6):  # 模拟攻击者持旧标识反复访问
        try:
            store.get(sid0)
            raise AssertionError("旧标识不应再可用")
        except InvalidSessionError as exc:
            rejected += 1
            print(f"  第 {attempt} 次用旧标识访问 -> 拒绝: {exc}")
    assert rejected == 5

    # 旧标识甚至不能再次用于登录（防重放）
    try:
        store.login(sid0, "alice", "s3cret-pass", authenticator)
        raise AssertionError("旧标识不应能再次登录")
    except InvalidSessionError as exc:
        print(f"  用旧标识再次登录       -> 拒绝: {exc}")

    s = store.get(sid1)
    print(f"审计指纹: 新会话记录的旧标识 SHA-256 指纹 = {s.prev_sid_fingerprint}")
    assert s.prev_sid_fingerprint is not None
    print("PASS: 旧标识 5/5 次访问被拒，且不能重放登录")


# ---------------------------------------------------------------- 场景 3
def test_consecutive_logins() -> None:
    hr("场景 3：连续多次登录 —— 每次都再更换（privilege 变化即轮换）")
    store = SessionStore()
    sid = store.create_session()
    chain = [sid]
    for round_no in range(1, 4):
        sid = store.login(sid, "alice", "s3cret-pass", authenticator)
        chain.append(sid)
        print(f"  第 {round_no} 次登录: {short(chain[-2])} -> {short(sid)} "
              f"(generation={store.get(sid).generation})")
    assert len(set(chain)) == len(chain), "每次登录都必须产生全新标识"
    for old in chain[:-1]:
        try:
            store.get(old)
            raise AssertionError("历史标识必须全部失效")
        except InvalidSessionError:
            pass
    print(f"标识轨迹: {' -> '.join(short(s) for s in chain)}")
    print("PASS: 3 次登录产生 4 个互不相同标识，历史标识全部失效")


# ---------------------------------------------------------------- 场景 4
def test_concurrent_logins() -> None:
    hr("场景 4：并发登录同一标识 —— 恰好 1 个成功，其余确定性地被拒绝")
    store = SessionStore()
    sid0 = store.create_session()
    n_threads = 8
    results, log = [], []
    log_lock = threading.Lock()
    barrier = threading.Barrier(n_threads)

    def worker(idx: int) -> None:
        barrier.wait()  # 尽量同时发起
        try:
            new_sid = store.login(sid0, "alice", "s3cret-pass", authenticator)
            outcome = ("OK", new_sid)
        except (InvalidSessionError, AuthenticationError) as exc:
            outcome = (type(exc).__name__, str(exc))
        with log_lock:
            results.append(outcome)
            log.append((idx, outcome[0],
                        short(outcome[1]) if outcome[0] == "OK" else "-"))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    winners = [r for r in results if r[0] == "OK"]
    losers = [r for r in results if r[0] == "InvalidSessionError"]
    for idx, status, sid_s in sorted(log):
        print(f"  线程 {idx}: {status:20s} {sid_s}")
    print(f"标识轨迹: {short(sid0)} --(锁内原子更换)--> {short(winners[0][1])}"
          f" ；其余 {len(losers)} 个线程看到旧标识已删除")
    assert len(winners) == 1, "并发下必须恰好一个登录成功"
    assert len(losers) == n_threads - 1
    assert store.active_count() == 1
    print("PASS: 8 线程并发 -> 恰好 1 成功、7 个 InvalidSessionError，行为确定")


# ---------------------------------------------------------------- 场景 5
def test_failed_login_retry() -> None:
    hr("场景 5：登录失败后重试 —— 失败不换标识，成功才换")
    store = SessionStore()
    sid0 = store.create_session()
    print(f"初始标识: {short(sid0)}")
    for attempt in range(1, 4):
        try:
            store.login(sid0, "alice", "wrong-pass", authenticator)
            raise AssertionError("错误密码不应登录成功")
        except AuthenticationError:
            s = store.get(sid0)
            print(f"  第 {attempt} 次失败: 标识不变 {short(sid0)} "
                  f"failed_attempts={s.failed_attempts} generation={s.generation}")
    sid1 = store.login(sid0, "alice", "s3cret-pass", authenticator)
    s = store.get(sid1)
    print(f"  第 4 次成功: {short(sid0)} -> {short(sid1)} "
          f"failed_attempts={s.failed_attempts} generation={s.generation}")
    assert s.failed_attempts == 3 and s.generation == 1
    print(f"标识轨迹: {short(sid0)}(失败x3,不变) -> {short(sid1)}(成功,更换)")
    print("PASS: 失败 3 次标识不变，成功时一次性更换")


# ---------------------------------------------------------------- 随机性
def test_randomness() -> None:
    hr("随机性来源与生成样本统计")
    import os
    import secrets

    print(f"来源: secrets.token_hex(32) -> os.urandom -> "
          f"{'getrandom()' if hasattr(os, 'getrandom') else '/dev/urandom'}"
          f"（内核 CSPRNG），每标识 256 bit 熵")
    n = 20000
    store = SessionStore()
    sids = [store.create_session() for _ in range(n)]

    assert all(len(s) == 64 for s in sids)
    assert all(c in "0123456789abcdef" for s in sids for c in s)
    uniq = len(set(sids))
    print(f"样本数: {n}，唯一值: {uniq}（碰撞数 {n - uniq}）")
    assert uniq == n

    bit_ones = sum(bin(int(s, 16)).count("1") for s in sids)
    bit_total = n * 256
    ratio = bit_ones / bit_total
    print(f"比特 1 占比: {ratio:.6f}（期望 0.5，总比特 {bit_total}）")
    assert abs(ratio - 0.5) < 0.01

    byte_counts = Counter()
    for s in sids:
        byte_counts.update(bytes.fromhex(s))
    expected = n * 32 / 256
    chi2 = sum((byte_counts.get(b, 0) - expected) ** 2 / expected
               for b in range(256))
    df = 255
    print(f"字节均匀性: chi2={chi2:.1f}（df={df}，期望≈{df}，"
          f"0.1% 临界值≈325）-> {'均匀' if chi2 < 325 else '异常'}")
    assert chi2 < 325

    print(f"样本前 5 个: {[short(s) for s in sids[:5]]}")
    collision_2_64 = (2 ** 64) ** 2 / (2 * 2 ** 256)
    print(f"生日界: 生成 2^64 个标识的碰撞概率 ≈ {collision_2_64:.2e}")
    print("PASS: 无碰撞、比特均衡、字节分布均匀")


# ---------------------------------------------------------------- 基准
def benchmark() -> None:
    hr("基准：100 万次登录会话操作（建匿名会话 -> 登录换标识 -> 销毁）")
    store = SessionStore()
    n = 1_000_000

    tracemalloc.start()
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    t0 = time.perf_counter()
    for _ in range(n):
        sid = store.create_session()
        sid = store.login(sid, "alice", "s3cret-pass", authenticator)
        store.logout(sid)
    elapsed = time.perf_counter() - t0
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    print(f"操作次数        : {n:,}")
    print(f"总耗时          : {elapsed:.2f} s")
    print(f"吞吐            : {n / elapsed:,.0f} ops/s"
          f"（每次操作含 2 次 CSPRNG 取随机数）")
    print(f"tracemalloc 峰值: {peak / 1024:.1f} KiB（Python 堆内分配）")
    print(f"进程 RSS 峰值   : {rss_after / 1024:.1f} MiB"
          f"（基准前 {rss_before / 1024:.1f} MiB）")
    print(f"结束后存活会话  : {store.active_count()}（旧标识均已删除，内存有界）")
    assert store.active_count() == 0


def main() -> None:
    test_first_login()
    test_old_id_invalidated()
    test_consecutive_logins()
    test_concurrent_logins()
    test_failed_login_retry()
    test_randomness()
    benchmark()
    hr("全部自测通过")


if __name__ == "__main__":
    main()
