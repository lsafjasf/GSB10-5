"""人类可读报告、阈值影响扫描、性能测量、报告落盘。"""
from __future__ import annotations

import json
import os
import platform
import resource
import sys
import time
import tracemalloc
from typing import List

from . import TAU_BODY, TAU_TITLE, diff_staged
from .benchgen import generate
from .fingerprint import jaccard, shingles
from .scenarios import SCENARIOS
from .summary import verify_roundtrip

KIND_CN = {"added": "新增", "deleted": "删除", "moved": "移动",
           "releveled": "层级调整", "renamed": "改名",
           "modified": "内容修改", "carried": "随迁(整章子节)"}
GROUP_ORDER = ("added", "deleted", "moved", "releveled",
               "renamed", "modified", "carried")


# ---------- 文本报告 ----------

def render_text(res: dict) -> str:
    s = res["summary"]
    lines = ["文档结构差异摘要", "=" * 40]
    lines.append(" | ".join(f"{KIND_CN[k]} {s['stats'].get(k, 0)}"
                            for k in GROUP_ORDER))
    lines.append(f"合计 {s['total']} 条变更记录 "
                 f"(tau_body={res['tau_body']}, tau_title={res['tau_title']})")
    by_id = {r["rid"]: r for r in res["records"]}
    for kind in GROUP_ORDER:
        ids = s["groups"].get(kind)
        if not ids:
            continue
        lines.append("")
        lines.append(f"## {KIND_CN[kind]} ({len(ids)})")
        for rid in ids:
            lines.append("  " + _record_line(by_id[rid]))
    return "\n".join(lines)


def _record_line(r: dict) -> str:
    oldp = " > ".join(r["old_path"]) if r["old_path"] else "-"
    newp = " > ".join(r["new_path"]) if r["new_path"] else "-"
    parts = [f"[{r['rid']}] {oldp} -> {newp}"]
    ev = r.get("evidence")
    if ev:
        parts.append(f"(标题相似度={ev['title_sim']}, 正文Jaccard={ev['body_jaccard']},"
                     f" 正文指纹一致={ev['body_fp_equal']})")
    if "struct_evidence" in r:
        se = r["struct_evidence"]
        parts.append(f"(父变化={se['parent_changed']}, 换位={se['reordered']},"
                     f" 层级差={se['delta_level']:+d})")
    if "subtree" in r:
        st = r["subtree"]
        parts.append(f"(子树 {st['sections']} 节 / {st['lines']} 行)")
    if r["kind"] == "carried":
        parts.append(f"(随 {r['carried_by']} 搬迁)")
    return " ".join(parts)


# ---------- 还原验证 ----------

def run_roundtrip_report() -> List[dict]:
    out = []
    for sc in SCENARIOS:
        res = diff_staged(sc["old"], sc["new"])
        v = verify_roundtrip(res["records"])
        v["scenario"] = sc["name"]
        v["desc"] = sc["desc"]
        out.append(v)
    return out


# ---------- 阈值影响扫描 ----------

def _content_case(replace: int) -> tuple:
    old_body = [f"阈值扫描案例第{i}行原始内容" for i in range(20)]
    new_body = list(old_body)
    for i in range(10, 10 + replace):
        new_body[i] = f"阈值扫描案例第{i}行改写内容"
    old = "# 章节\n" + "\n".join(old_body)
    new = "# 章节\n" + "\n".join(new_body)
    j = jaccard(shingles(old_body), shingles(new_body))
    return old, new, j


def _title_case(old_title: str, new_title: str) -> tuple:
    old = f"# 文档\n## {old_title}\n## 正文节\n正文内容行一\n正文内容行二"
    new = f"# 文档\n## {new_title}\n## 正文节\n正文内容行一\n正文内容行二"
    return old, new


def run_sweep(taus_body=(0.40, 0.50, 0.60, 0.70, 0.80, 0.90),
              taus_title=(0.50, 0.60, 0.70, 0.80, 0.90)) -> dict:
    # 表 1：正文配对阈值 tau_body（20 行正文连续改写 2/4/6 行）
    body_cases = []
    for replace in (2, 4, 6):
        old, new, j = _content_case(replace)
        body_cases.append({"name": f"20行正文连续改写{replace}行",
                           "old": old, "new": new, "jaccard": round(j, 4)})
    body_rows = []
    for tau in taus_body:
        for case in body_cases:
            res = diff_staged(case["old"], case["new"], tau_body=tau)
            body_rows.append({
                "tau": tau, "case": case["name"], "jaccard": case["jaccard"],
                "paired": len(res["matched"]) > 0,
                "kinds": sorted(r["kind"] for r in res["records"]),
            })
    # 表 2：无正文节配对阈值 tau_title（空正文节改名）
    title_cases = [
        {"name": "数据库备份 -> 数据库恢复", "sim": None,
         "old": _title_case("数据库备份", "数据库恢复")[0],
         "new": _title_case("数据库备份", "数据库恢复")[1]},
        {"name": "安装指南 -> 安装指引", "sim": None,
         "old": _title_case("安装指南", "安装指引")[0],
         "new": _title_case("安装指南", "安装指引")[1]},
        {"name": "安装指南 -> 安装手册", "sim": None,
         "old": _title_case("安装指南", "安装手册")[0],
         "new": _title_case("安装指南", "安装手册")[1]},
    ]
    from .fingerprint import title_sim
    from .model import norm_title
    for case in title_cases:
        old_t = case["name"].split(" -> ")[0]
        new_t = case["name"].split(" -> ")[1]
        case["sim"] = round(title_sim(norm_title(old_t), norm_title(new_t)), 4)
    title_rows = []
    for tau in taus_title:
        for case in title_cases:
            res = diff_staged(case["old"], case["new"], tau_title=tau)
            title_rows.append({
                "tau": tau, "case": case["name"], "title_sim": case["sim"],
                "paired": any(r["kind"] == "renamed" for r in res["records"]),
                "kinds": sorted(r["kind"] for r in res["records"]),
            })
    return {"taus_body": list(taus_body), "taus_title": list(taus_title),
            "default_tau_body": TAU_BODY, "default_tau_title": TAU_TITLE,
            "body_rows": body_rows, "title_rows": title_rows}


def format_sweep(sweep: dict) -> str:
    lines = ["# 阈值影响数据", ""]
    lines.append(f"默认阈值：tau_body={sweep['default_tau_body']} "
                 f"tau_title={sweep['default_tau_title']}")
    lines.append("")
    lines.append("## 表 1：正文配对阈值 tau_body 对归类的影响")
    lines.append("")
    lines.append("| tau_body | 案例 | 实测正文Jaccard | 是否配对 | 归类结果 |")
    lines.append("|---|---|---|---|---|")
    for r in sweep["body_rows"]:
        kinds = ", ".join(r["kinds"]) if r["kinds"] else "(无变更)"
        lines.append(f"| {r['tau']:.2f} | {r['case']} | {r['jaccard']:.4f} "
                     f"| {'是' if r['paired'] else '否'} | {kinds} |")
    lines.append("")
    lines.append("结论：Jaccard >= tau_body 才配对；配对成功归 `modified`，"
                 "失败则塌缩为 `deleted`+`added`。阈值越高，越多改写节被当成删增。")
    lines.append("")
    lines.append("## 表 2：无正文节配对阈值 tau_title 对归类的影响")
    lines.append("")
    lines.append("| tau_title | 案例 | 实测标题相似度 | 是否配对 | 归类结果 |")
    lines.append("|---|---|---|---|---|")
    for r in sweep["title_rows"]:
        kinds = ", ".join(r["kinds"]) if r["kinds"] else "(无变更)"
        lines.append(f"| {r['tau']:.2f} | {r['case']} | {r['title_sim']:.4f} "
                     f"| {'是' if r['paired'] else '否'} | {kinds} |")
    lines.append("")
    lines.append("结论：无正文节只有标题信号，title_sim >= tau_title 才配对；"
                 "配对成功归 `renamed`，失败则塌缩为 `deleted`+`added`。")
    return "\n".join(lines)


# ---------- 性能测量 ----------

def run_perf(trials: int = 3) -> dict:
    old_text, new_text = generate()
    old_lines = len(old_text.splitlines())
    new_lines = len(new_text.splitlines())

    # 计时（不开 tracemalloc，避免干扰）
    runs = []
    for _ in range(trials):
        t0 = time.perf_counter()
        res, stages = diff_staged(old_text, new_text, timed=True)
        total = time.perf_counter() - t0
        runs.append({"stages": stages, "total": total,
                     "records": len(res["records"]),
                     "matched": len(res["matched"]),
                     "summary": res["summary"]["stats"]})
    # 内存（单独一轮，tracemalloc 峰值 + 进程 RSS 峰值）
    tracemalloc.start()
    res = diff_staged(old_text, new_text)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "env": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
        },
        "docs": {"old_lines": old_lines, "new_lines": new_lines,
                 "old_bytes": len(old_text.encode("utf-8")),
                 "new_bytes": len(new_text.encode("utf-8")),
                 "old_sections": len(res["old"].sections),
                 "new_sections": len(res["new"].sections)},
        "trials": runs,
        "tracemalloc_peak_mb": round(peak / 1024 / 1024, 1),
        "max_rss_mb": round(rss_kb / 1024, 1),
    }


def format_perf(perf: dict) -> str:
    lines = ["# 性能数据（10 万行文档对）", ""]
    env, docs = perf["env"], perf["docs"]
    lines.append(f"- 环境：Python {env['python']} / {env['platform']} "
                 f"/ {env['machine']} / {env['cpu_count']} 核")
    lines.append(f"- 文档：v1 {docs['old_lines']} 行 {docs['old_bytes']} 字节 "
                 f"{docs['old_sections']} 节；v2 {docs['new_lines']} 行 "
                 f"{docs['new_bytes']} 字节 {docs['new_sections']} 节")
    lines.append("")
    lines.append("| 轮次 | 解析(s) | 指纹(s) | 配对(s) | 归类(s) | 合计(s) | 记录数 |")
    lines.append("|---|---|---|---|---|---|---|")
    for i, run in enumerate(perf["trials"], 1):
        st = run["stages"]
        lines.append(f"| {i} | {st['parse']:.3f} | {st['fingerprint']:.3f} "
                     f"| {st['match']:.3f} | {st['classify']:.3f} "
                     f"| {run['total']:.3f} | {run['records']} |")
    best = min(r["total"] for r in perf["trials"])
    med = sorted(r["total"] for r in perf["trials"])[len(perf["trials"]) // 2]
    lines.append(f"| 最优/中位 | | | | | {best:.3f} / {med:.3f} | |")
    lines.append("")
    lines.append(f"- 内存峰值（tracemalloc，Python 堆）：{perf['tracemalloc_peak_mb']} MB")
    lines.append(f"- 进程 RSS 峰值（ru_maxrss）：{perf['max_rss_mb']} MB")
    lines.append(f"- 变更归类结果：{json.dumps(perf['trials'][0]['summary'], ensure_ascii=False)}")
    return "\n".join(lines)


# ---------- 落盘 ----------

def write_reports(outdir: str = "reports") -> None:
    os.makedirs(outdir, exist_ok=True)
    rt = run_roundtrip_report()
    with open(os.path.join(outdir, "roundtrip.json"), "w", encoding="utf-8") as fh:
        json.dump(rt, fh, ensure_ascii=False, indent=2)
    print(f"{outdir}/roundtrip.json: {len(rt)} 个场景，全部通过 = "
          f"{all(v['summary_to_detail_ok'] and v['detail_to_summary_ok'] and v['stub_projection_ok'] for v in rt)}")
    sweep = run_sweep()
    with open(os.path.join(outdir, "threshold_sweep.md"), "w", encoding="utf-8") as fh:
        fh.write(format_sweep(sweep) + "\n")
    print(f"{outdir}/threshold_sweep.md: 已生成")
    perf = run_perf(trials=5)
    with open(os.path.join(outdir, "perf.md"), "w", encoding="utf-8") as fh:
        fh.write(format_perf(perf) + "\n")
    print(f"{outdir}/perf.md: 已生成")
