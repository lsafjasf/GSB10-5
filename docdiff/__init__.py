"""docdiff：文档版本结构差异工具（Python 3，仅标准库）。

用法见 RULES.md。核心入口：diff_text(old_text, new_text)。
"""
from __future__ import annotations

import time

from .classify import classify
from .fingerprint import fingerprint_tree
from .matcher import match
from .parser import parse
from .summary import summarize

TAU_BODY = 0.60    # 正文配对阈值：body_jaccard 下限
TAU_TITLE = 0.70   # 无正文节配对阈值：title_sim 下限


def diff_staged(old_text: str, new_text: str, tau_body: float = TAU_BODY,
                tau_title: float = TAU_TITLE, timed: bool = False):
    stages = {}
    t = time.perf_counter()
    old = parse(old_text)
    new = parse(new_text)
    stages["parse"] = time.perf_counter() - t
    t = time.perf_counter()
    fingerprint_tree(old)
    fingerprint_tree(new)
    stages["fingerprint"] = time.perf_counter() - t
    t = time.perf_counter()
    matched, scores = match(old, new, tau_body, tau_title)
    stages["match"] = time.perf_counter() - t
    t = time.perf_counter()
    records = classify(old, new, matched, scores, tau_title)
    stages["classify"] = time.perf_counter() - t
    result = {
        "old": old, "new": new,
        "matched": matched, "scores": scores,
        "records": records, "summary": summarize(records),
        "tau_body": tau_body, "tau_title": tau_title,
    }
    return (result, stages) if timed else result


def diff_text(old_text: str, new_text: str, tau_body: float = TAU_BODY,
              tau_title: float = TAU_TITLE) -> dict:
    return diff_staged(old_text, new_text, tau_body, tau_title)
