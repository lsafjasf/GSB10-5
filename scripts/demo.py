#!/usr/bin/env python3
"""演示：四类场景的复用率数据 + 复用校验数据。

运行：python3 scripts/demo.py
输出：终端表格 + results/reuse_report.json / results/verification_report.json
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from imglayer_cache import BuildContext, FileRecord, LayerCache, analyze, parse_script

RESULTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results")

BASE = """\
FROM base:1.0
WORKDIR /app
COPY app.py /app/app.py
RUN write /app/config.txt mode=prod
RUN mkdir /app/data && write /app/data/seed.txt v1
ENV APP_ENV production
RUN chmod 755 /app/app.py
"""

REORDERED = """\
FROM base:1.0
WORKDIR /app
RUN write /app/config.txt mode=prod
COPY app.py /app/app.py
RUN mkdir /app/data && write /app/data/seed.txt v1
ENV APP_ENV production
RUN chmod 755 /app/app.py
"""

MIDDLE_CHANGED = BASE.replace("mode=prod", "mode=debug")

TOUCH_CHOWN = BASE.replace(
    "RUN chmod 755 /app/app.py",
    "RUN chmod 755 /app/app.py && touch /app/config.txt && chown www-data /app/config.txt",
)

MODE_CHANGED_CTX = BASE  # 仅上下文里 app.py 的权限位变化


def ctx(mtime=111, uid=1000, gid=1000, mode=0o644, content=b"print('hello')"):
    return BuildContext(
        {
            "app.py": FileRecord(content=content, mode=mode, uid=uid, gid=gid, mtime_ns=mtime),
            "README.md": FileRecord(content=b"# demo", mode=0o644, uid=uid, gid=gid, mtime_ns=mtime + 1),
        }
    )


def run_case(name, script_a, ctx_a, script_b, ctx_b):
    cache = LayerCache()
    report = analyze(
        parse_script(script_b),
        ctx_b,
        cache,
        script_a=parse_script(script_a),
        context_a=ctx_a,
    )
    return name, report


def fmt_key(key: str) -> str:
    return key[:12]


def print_report(name, report):
    print("=" * 78)
    print(f"场景：{name}")
    print(
        f"  层数={report.total_layers}  复用={report.reused_layers}  重建={report.rebuilt_layers}"
        f"  层复用率={report.layer_reuse_ratio:.1%}  字节复用率={report.byte_reuse_ratio:.1%}"
        f"  首个重建层={report.first_rebuilt_index}"
    )
    print(f"  {'层':>2}  {'判定':<7}  {'键(前12)':<14}  校验                          指令")
    for v in report.verdicts:
        if v.decision == "reused":
            check = f"diff✓ 快照✓ 字节✓ ({v.blob_bytes}B)"
        else:
            check = "-"
        print(f"  {v.index:>2}  {v.decision:<7}  {fmt_key(v.key_b):<14}  {check:<29}  {v.instruction}")
    print(f"  最终视图一致性：{'一致 ✓' if report.final_snapshot_match else '不一致 ✗'}"
          f"  (snapshot={report.final_snapshot_digest[:16]}...)")


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    cases = [
        run_case("1. 完全相同（A == B）", BASE, ctx(), BASE, ctx()),
        run_case("2. 仅顺序调整（COPY 与 RUN 互换）", BASE, ctx(), REORDERED, ctx()),
        run_case("3. 中间一层变化（RUN mode=prod -> mode=debug）", BASE, ctx(), MIDDLE_CHANGED, ctx()),
        run_case(
            "4. 仅时间戳/所有者变化（上下文 mtime/uid/gid + RUN touch/chown）",
            BASE,
            ctx(),
            TOUCH_CHOWN,
            ctx(mtime=999_999_999, uid=4242, gid=777),
        ),
        run_case(
            "5. 反向用例：仅权限位变化（不得错误复用）",
            BASE,
            ctx(),
            MODE_CHANGED_CTX,
            ctx(mode=0o755),
        ),
        run_case(
            "6. 反向用例：仅内容变化（不得错误复用）",
            BASE,
            ctx(),
            MODE_CHANGED_CTX,
            ctx(content=b"print('tampered')"),
        ),
    ]

    reuse_rows = []
    verification_rows = []
    for name, report in cases:
        print_report(name, report)
        reuse_rows.append(
            {
                "scenario": name,
                "total_layers": report.total_layers,
                "reused_layers": report.reused_layers,
                "rebuilt_layers": report.rebuilt_layers,
                "first_rebuilt_index": report.first_rebuilt_index,
                "layer_reuse_ratio": round(report.layer_reuse_ratio, 4),
                "byte_reuse_ratio": round(report.byte_reuse_ratio, 4),
            }
        )
        for v in report.verdicts:
            verification_rows.append(
                {
                    "scenario": name,
                    "layer": v.index,
                    "instruction": v.instruction,
                    "decision": v.decision,
                    "diff_digest_replayed": (v.diff_digest_replayed or "")[:16],
                    "diff_digest_shadow": (v.diff_digest_shadow or "")[:16],
                    "digest_match": v.digest_match,
                    "byte_for_byte_match": v.byte_for_byte_match,
                }
            )

    print("=" * 78)
    print("汇总（复用率）")
    print(f"  {'场景':<46} {'复用/总层':>9} {'层复用率':>8} {'字节复用率':>9}")
    for row in reuse_rows:
        print(
            f"  {row['scenario']:<46} {row['reused_layers']:>4}/{row['total_layers']:<4}"
            f" {row['layer_reuse_ratio']:>8.1%} {row['byte_reuse_ratio']:>9.1%}"
        )

    with open(os.path.join(RESULTS_DIR, "reuse_report.json"), "w", encoding="utf-8") as fh:
        json.dump(reuse_rows, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(RESULTS_DIR, "verification_report.json"), "w", encoding="utf-8") as fh:
        json.dump(verification_rows, fh, ensure_ascii=False, indent=2)
    print(f"\n已写出 {RESULTS_DIR}/reuse_report.json 与 verification_report.json")


if __name__ == "__main__":
    main()
