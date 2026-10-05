#!/usr/bin/env python3
"""10 万行级文档对的耗时与内存基准。运行: python3 benchmark.py"""

import json
import os
import random
import resource
import sys
import time
import tracemalloc

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from docdiff import diff_documents, verify_roundtrip

SEED = 42
N_CHAPTERS = 100          # L2
SECTIONS_PER_CHAPTER = 5  # L3
SUBS_PER_SECTION = 4      # L4
LINES_PER_SUBSECTION = 46
# 章节数: 100 + 100*5 + 100*5*4 = 2600；正文行: 2000*46 = 92000
# 加上标题行与空行 => 总行数约 100000

WORDS = ("系统 模块 配置 服务 数据 接口 缓存 队列 日志 用户 权限 任务 "
         "节点 集群 部署 监控 告警 指标 策略 版本 实例 请求 响应 超时 "
         "重试 熔断 限流 路由 表 索引 文件 目录 进程 线程 内存 磁盘 网络").split()


def make_body(rng, n_lines):
    return [" ".join(rng.choice(WORDS) for _ in range(8))
            for _ in range(n_lines)]


def gen_old(rng):
    lines = ["# 基准测试手册", "", "这是自动生成的基准文档前言。"]
    for c in range(N_CHAPTERS):
        lines += ["", f"## 第{c:03d}章 主题{c}", ""]
        lines += make_body(rng, 2)
        for s in range(SECTIONS_PER_CHAPTER):
            lines += ["", f"### 第{c:03d}章 节{s} 概要", ""]
            lines += make_body(rng, 2)
            for u in range(SUBS_PER_SECTION):
                lines += ["", f"#### 小节 {c:03d}-{s}-{u} 操作细则", ""]
                lines += make_body(rng, LINES_PER_SUBSECTION)
    return lines


def gen_new(rng, old_lines):
    """在旧版基础上施加受控的结构变更，得到行数基本相同的新版。"""
    # 解析为 (章节块) 操作代价高，这里直接按行做确定性变换:
    lines = list(old_lines)
    out = []
    i = 0
    n = len(lines)
    deleted = added = renamed = moved = leveled = modified = 0
    moved_blocks = []
    while i < n:
        line = lines[i]
        if line.startswith("## 第") and not line.startswith("###"):
            cnum = int(line[4:7])
            # 收集整个 L2 章块（到下一个 ## 或文末）
            j = i + 1
            while j < n and not (lines[j].startswith("## ") and
                                 not lines[j].startswith("###")):
                j += 1
            block = lines[i:j]
            if cnum % 20 == 19:      # 5% 删除
                deleted += 1
            elif cnum % 20 == 18:    # 5% 整章移动（暂存，稍后插回）
                moved_blocks.append(block)
                moved += 1
            else:
                if cnum % 20 == 17:  # 5% 改名
                    block = [block[0].replace(f"主题{cnum}", f"改名主题{cnum}")] + block[1:]
                    renamed += 1
                out.extend(block)
            i = j
        else:
            out.append(line)
            i += 1
    # 插入移动块 + 新增章（新增章替换被删章的行数）
    insert_at = len(out) // 2
    for block in moved_blocks:
        out[insert_at:insert_at] = block
        insert_at += len(block)
    for k in range(deleted):
        cnum = 900 + k
        block = ["", f"## 新增第{cnum}章 全新主题", ""]
        block += make_body(rng, 2)
        for s in range(SECTIONS_PER_CHAPTER):
            block += ["", f"### 新增第{cnum}章 节{s} 概要", ""]
            block += make_body(rng, 2)
            for u in range(SUBS_PER_SECTION):
                block += ["", f"#### 新增小节 {cnum}-{s}-{u} 操作细则", ""]
                block += make_body(rng, LINES_PER_SUBSECTION)
        out[insert_at:insert_at] = block
        insert_at += len(block)
        added += 1
    # 层级调整: 每 25 章挑 1 章，把其 L4 标题降一级(#### -> ###)
    final = []
    cur_chapter = -1
    for line in out:
        if line.startswith("## ") and not line.startswith("###"):
            cur_chapter += 1
        if (line.startswith("#### 小节") and cur_chapter % 25 == 7):
            line = line[1:]  # #### -> ###
            leveled += 1
        final.append(line)
    # 内容修改: 每 50 行正文中改 1 行（约 2%）
    for idx in range(0, len(final), 50):
        if final[idx] and not final[idx].startswith("#"):
            final[idx] = final[idx] + " 修订"
            modified += 1
    stats = {"deleted_chapters": deleted, "added_chapters": added,
             "renamed_chapters": renamed, "moved_chapters": moved,
             "level_changed_headings": leveled, "modified_lines": modified}
    return final, stats


def main():
    rng = random.Random(SEED)
    old_lines = gen_old(rng)
    new_lines, change_stats = gen_new(rng, old_lines)
    old_text, new_text = "\n".join(old_lines), "\n".join(new_lines)
    print(f"旧版行数: {len(old_lines)}  新版行数: {len(new_lines)}")
    print(f"施加的变更: {json.dumps(change_stats, ensure_ascii=False)}")

    # 计时（不带 tracemalloc，避免其插桩拖慢）
    t0 = time.perf_counter()
    diff = diff_documents(old_text, new_text)
    elapsed = time.perf_counter() - t0
    # 内存峰值（单独再跑一次，tracemalloc 统计 Python 堆）
    tracemalloc.start()
    diff_documents(old_text, new_text)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    rt = verify_roundtrip(diff)
    report = {
        "old_lines": len(old_lines),
        "new_lines": len(new_lines),
        "old_sections": diff.old_section_count,
        "new_sections": diff.new_section_count,
        "matched_pairs": diff.matched_pairs,
        "elapsed_seconds": round(elapsed, 3),
        "tracemalloc_peak_mb": round(peak / 1024 / 1024, 1),
        "max_rss_mb": round(rss_kb / 1024, 1),
        "stats": diff.stats(),
        "roundtrip_ok": rt["ok"],
        "change_stats_applied": change_stats,
    }
    os.makedirs("reports", exist_ok=True)
    with open("reports/benchmark.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
