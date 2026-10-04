#!/usr/bin/env python3
"""基准：100 万候选、10 万次补全。

输出：
- 补全耗时分布（min/p50/p90/p95/p99/p99.9/max，单位 µs）
- 按前缀类型分桶的耗时分布
- 内存：索引构建后 RSS、基准结束 RSS、进程峰值 RSS
- 权限校验：10 万次补全中无权限名称出现次数 + 抽样暴力复核
- 权限变更（grant/revoke）自身耗时分布

运行：python3 benchmark.py
"""
from __future__ import annotations

import random
import statistics
import time
import zlib
from bisect import bisect_left

from completion import ACL, CompletionIndex

N_ITEMS = 1_000_000
N_QUERIES = 100_000
LIMIT = 10
SEED = 20261005

SERVICES = ("api", "auth", "billing", "cache", "db", "gateway", "mail",
            "queue", "search", "storage", "user", "worker")
TEAMS = ("alpha", "beta", "gamma", "delta", "omega")
N_GROUPS = 20
ADMIN_GROUP = 999
FIN_GROUP = 777


def rss_kb() -> int:
    with open("/proc/self/status") as fh:
        for line in fh:
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    return -1


def peak_kb() -> int:
    with open("/proc/self/status") as fh:
        for line in fh:
            if line.startswith("VmHWM:"):
                return int(line.split()[1])
    return -1


def fmt_mb(kb: int) -> str:
    return f"{kb / 1024:.1f} MB"


def quantiles(sorted_us, ps=(0.5, 0.9, 0.95, 0.99, 0.999)):
    n = len(sorted_us)
    out = {}
    for p in ps:
        idx = min(n - 1, max(0, int(p * (n - 1) + 0.5)))
        out[p] = sorted_us[idx]
    return out


def report(name, samples_us):
    samples_us.sort()
    q = quantiles(samples_us)
    line = (f"{name:<28} n={len(samples_us):>6} "
            f"min={samples_us[0]:>8.1f}us "
            + " ".join(f"p{int(p*1000)/10:g}={q[p]:>8.1f}us" for p in q)
            + f" max={samples_us[-1]:>9.1f}us mean={statistics.fmean(samples_us):>8.1f}us")
    print(line)
    return line


def gen_items():
    """生成 100 万条候选：约 20% 属于受限组，另含 1000 条敏感名称。"""
    items = []
    n_regular = 999_000
    n_cells = len(SERVICES) * len(TEAMS)
    per_cell, remainder = divmod(n_regular, n_cells)
    cells = [(svc, team) for svc in SERVICES for team in TEAMS]
    for cell_idx, (svc, team) in enumerate(cells):
        count = per_cell + (remainder if cell_idx == 0 else 0)
        for i in range(count):
            name = f"{svc}-{team}-{i:05d}"
            group = None
            if zlib.crc32(name.encode()) % 5 == 0:  # ~20% 受限
                group = zlib.crc32(name.encode()) % N_GROUPS
            items.append((name, group))
    for i in range(500):
        items.append((f"secret-admin-{i:04d}", ADMIN_GROUP))
        items.append((f"secret-finance-{i:04d}", FIN_GROUP))
    assert len(items) == N_ITEMS, len(items)
    return items


def gen_queries(rng, names):
    """生成 10 万个查询：70% 真实名称前缀、10% 敏感前缀、10% 重前缀、10% 无匹配。"""
    queries = []
    n_real = int(N_QUERIES * 0.70)
    for _ in range(n_real):
        name = names[rng.randrange(len(names))]
        cut = rng.randint(1, min(len(name), 8))
        queries.append(name[:cut])
    for _ in range(int(N_QUERIES * 0.10)):
        queries.append(rng.choice(("secret-", "secret-admin-", "secret-finance-")))
    for _ in range(int(N_QUERIES * 0.10)):
        queries.append(rng.choice(("a", "api-", "db-", "s", "se")))
    for _ in range(N_QUERIES - len(queries)):
        queries.append("zz-" + str(rng.randrange(1000)))
    rng.shuffle(queries)
    return queries


def main():
    rng = random.Random(SEED)
    print(f"=== 生成 {N_ITEMS:,} 条候选 ===")
    t0 = time.perf_counter()
    items = gen_items()
    print(f"生成耗时 {time.perf_counter() - t0:.1f}s，RSS={fmt_mb(rss_kb())}")

    print("=== 构建索引（排序） ===")
    t0 = time.perf_counter()
    index = CompletionIndex(items)
    build_s = time.perf_counter() - t0
    names = index.names()
    del items  # 释放原始 (name, group) 列表，只保留索引
    import gc; gc.collect()
    rss_after_build = rss_kb()
    print(f"索引构建 {build_s:.2f}s，索引驻留 RSS={fmt_mb(rss_after_build)}")

    acl = ACL()
    acl.set_groups(
        grants={
            "alice": {1, 5, 9},
            "bob": set(range(N_GROUPS)),
            "carol": {FIN_GROUP},
        },
        superusers={"root"},
    )
    # 用户池：carol 权重最高（只能看到 ~80% 公开项，过滤压力最大）
    users = (["carol"] * 4 + ["alice"] * 3 + ["dave"] * 2 + ["bob"] + ["root"])
    queries = gen_queries(rng, names)

    print(f"=== 基准：{N_QUERIES:,} 次补全（limit={LIMIT}） ===")
    lat_us = []
    by_kind = {"real": [], "sensitive": [], "heavy": [], "miss": []}
    violations = 0
    scanned_total = 0
    t_start = time.perf_counter()
    for i, prefix in enumerate(queries):
        user = users[i % len(users)]
        t0 = time.perf_counter_ns()
        res = index.complete(prefix, user, acl, limit=LIMIT)
        dt = (time.perf_counter_ns() - t0) / 1000.0
        lat_us.append(dt)
        scanned_total += res.scanned
        if prefix.startswith("secret"):
            by_kind["sensitive"].append(dt)
        elif prefix.startswith("zz-"):
            by_kind["miss"].append(dt)
        elif prefix in ("a", "api-", "db-", "s", "se"):
            by_kind["heavy"].append(dt)
        else:
            by_kind["real"].append(dt)
        # 权限校验：返回的每一项都必须对该用户可见（在返回前已过滤）
        snap_grants = acl.snapshot().groups_of(user)
        is_super = acl.snapshot().is_superuser(user)
        for _, group in res.items:
            if not (group is None or is_super or group in snap_grants):
                violations += 1
    total_s = time.perf_counter() - t_start

    print(f"总耗时 {total_s:.2f}s，QPS={N_QUERIES / total_s:,.0f}，"
          f"平均扫描 {scanned_total / N_QUERIES:.1f} 条/次")
    print()
    print("--- 补全耗时分布（µs） ---")
    report("all", lat_us)
    for kind, samples in by_kind.items():
        report(f"prefix:{kind}", samples)
    print()

    print("--- 权限校验 ---")
    print(f"10 万次补全返回项逐个复核，无权限名称出现次数: {violations}")
    # 抽样暴力复核：在排序索引的前缀区间内做线性权限过滤
    mismatches = 0
    all_names = index.names()
    all_groups = [g for _, g in index.items()]
    spot = rng.sample(range(N_QUERIES), 200)
    for i in spot:
        prefix = queries[i]
        user = users[i % len(users)]
        res = index.complete(prefix, user, acl, limit=LIMIT)
        grants = acl.snapshot().groups_of(user)
        is_super = acl.snapshot().is_superuser(user)
        start = bisect_left(all_names, prefix)
        end = (bisect_left(all_names, prefix[:-1] + chr(ord(prefix[-1]) + 1))
               if prefix else len(all_names))
        expected = []
        for j in range(start, end):
            group = all_groups[j]
            if group is None or is_super or group in grants:
                expected.append((all_names[j], group))
                if len(expected) >= LIMIT:
                    break
        if list(res.items) != expected:
            mismatches += 1
    print(f"抽样 200 次与暴力参考实现对比，不一致次数: {mismatches}")
    # 敏感名称泄露专项检查：按权限快照逐个判定可见性
    sensitive_leaks = 0
    snap = acl.snapshot()
    for user in ("carol", "alice", "dave"):
        res = index.complete("secret-", user, acl, limit=1000)
        sensitive_leaks += sum(1 for _, g in res.items if not snap.visible(g, user))
    print(f"非授权用户补全 'secret-' 前缀，越权敏感名称泄露数: {sensitive_leaks}")
    carol_res = index.complete("secret-", "carol", acl, limit=1000)
    print(f"  对照: carol 已授权组 {FIN_GROUP}，合法可见敏感名称数: "
          f"{len(carol_res.names)}（应=500 条 secret-finance-*）")
    dave_res = index.complete("secret-", "dave", acl, limit=1000)
    print(f"  对照: dave 无任何授权，可见敏感名称数: {len(dave_res.names)}（应=0）")
    root_res = index.complete("secret-", "root", acl, limit=1000)
    print(f"  对照: 超级用户 root 可见敏感名称数: {len(root_res.names)}（应=1000）")
    print()

    print("--- 权限变更耗时（grant/revoke 各 1000 次） ---")
    grant_us, revoke_us = [], []
    for g in range(1000):
        t0 = time.perf_counter_ns()
        acl.grant("bench-user", [g % N_GROUPS])
        grant_us.append((time.perf_counter_ns() - t0) / 1000.0)
        t0 = time.perf_counter_ns()
        acl.revoke("bench-user", [g % N_GROUPS])
        revoke_us.append((time.perf_counter_ns() - t0) / 1000.0)
    report("acl.grant", grant_us)
    report("acl.revoke", revoke_us)
    print()

    print("--- 内存 ---")
    print(f"索引构建后 RSS : {fmt_mb(rss_after_build)}")
    print(f"基准结束时 RSS : {fmt_mb(rss_kb())}")
    print(f"进程峰值 RSS   : {fmt_mb(peak_kb())}")

    print()
    print("=== 小候选集对照（30 条候选，2 万次补全） ===")
    small_items = [(f"cmd-{i:02d}", None if i % 3 else 7) for i in range(30)]
    small_index = CompletionIndex(small_items)
    small_lat = []
    for i in range(20_000):
        prefix = f"cmd-{i % 10}"
        t0 = time.perf_counter_ns()
        small_index.complete(prefix, "alice", acl, limit=LIMIT)
        small_lat.append((time.perf_counter_ns() - t0) / 1000.0)
    report("small(30 items)", small_lat)

    ok = violations == 0 and mismatches == 0 and sensitive_leaks == 0
    print()
    print("结果:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
