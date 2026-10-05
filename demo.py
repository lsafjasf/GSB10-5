"""演示：不相干 / 时间重叠 / 资源重叠 / 传递性冲突，以及最早可排期方案。

运行：python3 demo.py
"""

from release_conflict import Conflict, Release, Resource, detect_conflicts, schedule


def fmt(t: int) -> str:
    """把"距当天 00:00 的分钟数"格式化为 HH:MM。"""
    return f"{t // 60:02d}:{t % 60:02d}"


def build_scenario():
    resources = {
        "db-primary": Resource("db-primary", capacity=1),    # 主库：独占
        "redis": Resource("redis", capacity=1),              # 缓存：独占
        "gateway": Resource("gateway", capacity=1),          # 网关：独占
        "feature-pool": Resource("feature-pool", capacity=2),  # 特性灰度池：最多 2 个并行
    }

    releases = [
        # A 与 B：时间完全重叠，但动的是不相干资源 —— 不冲突
        Release("A-db-upgrade", 10 * 60, 11 * 60, {"db-primary": 1}),
        Release("B-gateway-rollout", 10 * 60, 11 * 60, {"gateway": 1}),
        # C 与 D：都要主库，但占用时段只有端点相接（半开区间）—— 不冲突
        Release("C-db-index", 11 * 60, 12 * 60, {"db-primary": 1}),
        Release("D-db-backup", 12 * 60, 13 * 60, {"db-primary": 1}),
        # E 与 F：同资源 + 时间重叠 —— 两元冲突
        Release("E-redis-flush", 13 * 60, 14 * 60, {"redis": 1}),
        Release("F-redis-config", 13 * 60 + 30, 14 * 60 + 30, {"redis": 1}),
        # X / Y / Z：feature-pool 容量 2，两两都不冲突，三个一起超载 —— 传递性冲突
        Release("X-feature", 15 * 60, 17 * 60, {"feature-pool": 1}),
        Release("Y-feature", 15 * 60 + 30, 17 * 60 + 30, {"feature-pool": 1}),
        Release("Z-feature", 16 * 60, 16 * 60 + 30, {"feature-pool": 1}),
    ]
    return resources, releases


def print_conflicts(conflicts):
    print("=" * 72)
    print("冲突清单（最小冲突集，定位到共享资源与发布单）")
    print("=" * 72)
    if not conflicts:
        print("（无冲突）")
    for c in conflicts:
        kind = "两元冲突" if c.size == 2 else f"{c.size} 元冲突（传递性）"
        print(
            f"- [{kind}] 资源={c.resource}  发布单={' × '.join(c.releases)}  "
            f"共同占用=[{fmt(c.overlap[0])}, {fmt(c.overlap[1])})  "
            f"需求={c.demand} > 容量={c.capacity}"
        )


def print_schedule(resources, releases):
    plan = schedule(releases, resources)

    # 自检：按排期方案执行不应再产生任何冲突
    executed = [
        Release(s.release.id, s.start, s.end, dict(s.release.resource_demands))
        for s in plan
    ]
    assert detect_conflicts(executed, resources) == [], "排期方案自检失败：仍存在冲突"

    print()
    print("=" * 72)
    print("最早可排期方案（按期望开始时间升序逐个安排，均已验证无冲突）")
    print("=" * 72)
    for s in sorted(plan, key=lambda x: (x.start, x.release.id)):
        r = s.release
        moved = "" if s.start == r.start else f"  <- 原窗口 {fmt(r.start)}-{fmt(r.end)}"
        print(
            f"- {r.id:<20} 最早可安排 {fmt(s.start)}-{fmt(s.end)}"
            f"  占用={','.join(r.resource_demands)}{moved}"
        )


def main():
    resources, releases = build_scenario()
    conflicts = detect_conflicts(releases, resources)
    print_conflicts(conflicts)
    print_schedule(resources, releases)


if __name__ == "__main__":
    main()
