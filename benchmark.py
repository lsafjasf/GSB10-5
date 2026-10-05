"""性能基准：10 万条术语、100 万字文本的耗时与内存峰值。

运行：python3 benchmark.py
"""

import random
import resource
import time
import tracemalloc

from termcheck import Checker, Entry, Termbase

N_TERMS = 100_000
N_CHARS = 1_000_000
SEED = 20261005

CJK_POOL = ("计算 网络 存储 数据 模型 训练 推理 服务 接口 协议 缓存 队列 内核 驱动 编译 "
            "部署 监控 日志 安全 加密 认证 授权 事务 索引 分片 复制 容灾 弹性 负载 均衡").split()
LATIN_POOL = ("server client buffer cache socket thread process kernel driver compiler "
              "runtime database index query transaction cluster node replica shard proxy").split()


def gen_termbase(n, rng):
    entries = []
    for i in range(n):
        if i % 3 == 2:
            approved = f"{rng.choice(LATIN_POOL)}{i:06d}"
            variant = f"old{approved}"
        else:
            approved = "".join(rng.choice(CJK_POOL) for _ in range(rng.randint(2, 3))) + f"{i:05d}"
            variant = "旧译" + approved
        variants = [variant] if i % 10 < 3 else []  # 30% 的条目登记 1 个变体
        entries.append(Entry(f"B{i:06d}", approved, variants=variants))
    return entries


def gen_text(n, entries, rng):
    """生成约 n 字的混合文本，并撒入 1000 个已知变体冲突。"""
    zh = "的了一是我不在人们有来他这上着个地到大里说就去子得也和那要下看天时过出小么起你都把好还多没为又可家学只以主会样年想生同老中十从自面前头道它后然走很像见两用她国动进成回什边作对开而己些现山民候经发工向事命给长水几义三声于高手知理眼志点心战二问但身方实吃做叫当住听革打呢真全才四已所敌之最光产情路分总条白话东席次亲如被花口放儿常气五第使写军吧文运再果怎定许快明行因别飞外树物活部门无往船望新带队先力完却站代员机更九您每风级跟笑啊孩万少直意夜比阶连车重便斗马哪化太指变社似士者干石满日决百原拿群究各六本思解立河村八难早论吗根共让相研今其书坐接应关信觉步反处记将千找争领或师结块跑谁草越字加脚紧爱等习阵怕月青半火法题建赶位唱海七女任件感准张团屋离色脸片科倒睛利世刚且由送切星导晚表够整认响雪流未场该并底深刻平伟忙提确近亮轻讲农古黑告界拉名呀土清阳照办史改历转画造嘴此治北必服雨穿内识验传业菜爬睡兴形量咱观苦体众通冲合破友度术饭公旁房极南枪读沙岁线野坚空收算至政城劳落钱特围弟胜教热展包歌类渐强数乡呼性音答哥际旧神念展"
    latin_words = LATIN_POOL * 4
    parts = []
    total = 0
    while total < n - 2000:
        if rng.random() < 0.3:
            w = rng.choice(latin_words) + " "
        else:
            w = "".join(rng.choice(zh) for _ in range(rng.randint(5, 20)))
        parts.append(w)
        total += len(w)
    text = "".join(parts)
    # 撒入 1000 个变体冲突：把文本等分为 1000 段，每段注入一次，
    # 用标点保证注入点互不重叠且满足拉丁词边界（模拟真实句子中的出现方式）。
    with_var = [e for e in entries if e.variants]
    n_inject = 1000
    seg = len(text) // n_inject
    pieces = []
    for k in range(n_inject):
        chunk = text[k * seg:(k + 1) * seg]
        pieces.append(chunk)
        e = with_var[k % len(with_var)]
        pieces.append("，" + e.variants[0] + "，")
    pieces.append(text[n_inject * seg:])
    return "".join(pieces)


def peak_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # Linux: KB


def main():
    rng = random.Random(SEED)
    print(f"生成 {N_TERMS} 条术语与 {N_CHARS} 字文本 ...")
    entries = gen_termbase(N_TERMS, rng)
    text = gen_text(N_CHARS, entries, rng)
    print(f"文本实际长度：{len(text)} 字")

    t0 = time.perf_counter()
    checker = Checker(Termbase(entries))
    t1 = time.perf_counter()
    build_s = t1 - t0

    tracemalloc.start()
    t0 = time.perf_counter()
    findings = checker.check(text, source="bench.txt")
    t1 = time.perf_counter()
    scan_s = t1 - t0
    _, py_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    errors = sum(1 for f in findings if f.severity == "error")
    nodes = len(checker.automaton.children)
    print(f"自动机节点数：{nodes:,}，模式数：{len(checker.automaton.patterns):,}")
    print(f"构建耗时：{build_s:.2f} s")
    print(f"检查耗时：{scan_s:.2f} s（{len(text) / scan_s / 1e4:.1f} 万字/秒）")
    print(f"总耗时：{build_s + scan_s:.2f} s")
    print(f"检出冲突：{errors} 条（撒入 1000 处，验证匹配链路有效）")
    print(f"进程峰值内存 ru_maxrss：{peak_mb():.0f} MB")
    print(f"检查阶段 Python 堆峰值（tracemalloc）：{py_peak / 1e6:.0f} MB")

    with open("reports/benchmark.md", "w", encoding="utf-8") as fh:
        fh.write("# 性能基准（10 万条术语 × 100 万字文本）\n\n")
        fh.write(f"- 术语规模：{N_TERMS:,} 条（30% 含变体译名），自动机节点 {nodes:,}，模式 {len(checker.automaton.patterns):,} 个\n")
        fh.write(f"- 文本规模：{len(text):,} 字（中英混合，撒入 1000 处已知冲突）\n")
        fh.write(f"- 构建自动机：{build_s:.2f} s\n")
        fh.write(f"- 扫描检查：{scan_s:.2f} s\n")
        fh.write(f"- 总耗时：{build_s + scan_s:.2f} s\n")
        fh.write(f"- 检出冲突：{errors} 条\n")
        fh.write(f"- 内存峰值（进程 ru_maxrss）：{peak_mb():.0f} MB\n")
        fh.write(f"- 检查阶段 Python 堆峰值（tracemalloc）：{py_peak / 1e6:.0f} MB\n")
    print("已写入 reports/benchmark.md")


if __name__ == "__main__":
    main()
