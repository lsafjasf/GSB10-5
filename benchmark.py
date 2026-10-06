"""对 10 万条译文做占位符校验，输出耗时与内存峰值（tracemalloc）。"""

import random
import resource
import time
import tracemalloc

from placeholder_lib import validate

N = 100_000

SPECS = ["%s", "%d", "%.2f", "%05d", "%(user)s", "%(amount).2f", "%-8s", "%+d"]
WORDS = ["你好", "消息", "订单", "支付", "成功", "失败", "用户", "余额",
         "hello", "order", "payment", "user", "balance", "done"]


def make_pair(rng: random.Random, idx: int):
    k = rng.randint(1, 5)
    specs = [rng.choice(SPECS) for _ in range(k)]
    src = " ".join(
        w for pair in zip([rng.choice(WORDS) for _ in range(k)], specs)
        for w in pair
    )
    tgt_specs = list(specs)
    # 90% 仅乱序，10% 制造缺失/多余/次数不一致
    if idx % 10 == 9:
        op = rng.choice(["missing", "extra", "dup"])
        if op == "missing" and tgt_specs:
            tgt_specs.pop(rng.randrange(len(tgt_specs)))
        elif op == "extra":
            tgt_specs.append(rng.choice(SPECS))
        elif op == "dup" and tgt_specs:
            tgt_specs.append(tgt_specs[0])
    rng.shuffle(tgt_specs)
    tgt = " ".join(
        w for pair in zip([rng.choice(WORDS) for _ in range(len(tgt_specs))], tgt_specs)
        for w in pair
    )
    return src, tgt


def main():
    rng = random.Random(42)
    pairs = [make_pair(rng, i) for i in range(N)]

    tracemalloc.start()
    t0 = time.perf_counter()
    ok_count = 0
    bad = {"missing": 0, "extra": 0, "count_mismatch": 0}
    for src, tgt in pairs:
        r = validate(src, tgt)
        if r.ok:
            ok_count += 1
        else:
            bad["missing"] += bool(r.missing)
            bad["extra"] += bool(r.extra)
            bad["count_mismatch"] += bool(r.count_mismatches)
    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print(f"校验条数:        {N}")
    print(f"总耗时:          {elapsed:.3f} 秒")
    print(f"平均每条:        {elapsed / N * 1e6:.2f} 微秒")
    print(f"吞吐:            {N / elapsed:,.0f} 条/秒")
    print(f"内存峰值:        {peak / 1024 / 1024:.2f} MiB (tracemalloc, 校验循环内)")
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(f"进程峰值 RSS:    {rss / 1024:.2f} MiB (含 10 万条数据本身)")
    print(f"通过:            {ok_count}")
    print(f"含缺失:          {bad['missing']}")
    print(f"含多余:          {bad['extra']}")
    print(f"含次数不一致:    {bad['count_mismatch']}")


if __name__ == "__main__":
    main()
