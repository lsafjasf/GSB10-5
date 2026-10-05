"""基准数据生成：构造带期望场景标注的原文/译文对。

场景构成（权重，合计 100）：
  ok_same        30  占位符顺序不变、文本翻译
  ok_reorder     30  占位符打乱顺序（多重集一致）
  missing        10  删掉一个唯一占位符
  extra          10  增加一个全新身份的占位符
  count_mismatch 10  重复占位符次数 +1
  invalid         5  含非法占位符（未知转换字符 / 悬空 %）
  drift           5  身份不变、仅宽度变化（默认通过，strict 模式报漂移）
三种语法家族（命名 / 位置 / 裸占位符）轮流出现。
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, List

SCENARIOS = (
    "ok_same",
    "ok_reorder",
    "missing",
    "extra",
    "count_mismatch",
    "invalid",
    "drift",
)
_WEIGHTS = (30, 30, 10, 10, 10, 5, 5)

FAMILIES = [
    {
        "name": "named",
        "tokens": ["%(user)s", "%(count)03d", "%(price).2f", "%(user)s"],
        "glue_src": ["用户 ", " 有 ", " 条未读，合计 ", " 元，提醒 ", "。"],
        "glue_tgt": ["User ", " has ", " unread, total ", " CNY, remind ", "."],
        "values": {"user": "alice", "count": 7, "price": 3.5},
        "drop_index": 1,
        "extra_token": "%(ghost)s",
        "dup_token": "%(user)s",
        "drift": ("%(count)03d", "%(count)3d"),
    },
    {
        "name": "positional",
        "tokens": ["%1$06d", "%2$s", "%3$.2f", "%4$.1f"],
        "glue_src": ["订单 ", " 创建于 ", "，金额 ", " 元，税率 ", "%%"],
        "glue_tgt": ["Order ", " created at ", ", amount ", " CNY, tax ", "%%"],
        "values": [42, "2026-10-05", 199.9, 6.5],
        "drop_index": 2,
        "extra_token": "%9$s",
        "dup_token": "%1$06d",
        "drift": ("%1$06d", "%1$6d"),
    },
    {
        "name": "bare",
        "tokens": ["%s", "%03d", "%.1f"],
        "glue_src": ["Hello ", ", you have ", " messages (", "%%)"],
        "glue_tgt": ["你好 ", "，你有 ", " 条消息（", "%%）"],
        "values": ["carol", 7, 98.25],
        "drop_index": 1,
        "extra_token": "%x",
        "dup_token": "%s",
        "drift": ("%03d", "%3d"),
    },
]


@dataclass(frozen=True)
class Pair:
    family: str
    scenario: str
    source: str
    translation: str
    values: Any


def _render(tokens: List[str], glue: List[str]) -> str:
    out = []
    for i, tok in enumerate(tokens):
        out.append(glue[i])
        out.append(tok)
    out.append(glue[len(tokens)])
    return "".join(out)


def _translate(family: dict, scenario: str, rng: random.Random) -> str:
    tokens = list(family["tokens"])
    glue = list(family["glue_tgt"])
    if scenario == "ok_same":
        pass
    elif scenario == "ok_reorder":
        rng.shuffle(tokens)
    elif scenario == "missing":
        del tokens[family["drop_index"]]
    elif scenario in ("extra", "count_mismatch"):
        extra = family["extra_token"] if scenario == "extra" else family["dup_token"]
        tokens.append(extra)
        glue = glue[:-1] + [glue[-1] + " ", ""]
    elif scenario == "invalid":
        return _render(tokens, glue) + (" 剩余 %z" if rng.random() < 0.5 else " 进度 %")
    elif scenario == "drift":
        old, new = family["drift"]
        tokens[tokens.index(old)] = new
    else:
        raise ValueError(scenario)
    return _render(tokens, glue)


def generate(n: int = 100000, seed: int = 42) -> List[Pair]:
    rng = random.Random(seed)
    weighted = [s for s, w in zip(SCENARIOS, _WEIGHTS) for _ in range(w)]
    pairs: List[Pair] = []
    for i in range(n):
        family = FAMILIES[i % len(FAMILIES)]
        scenario = rng.choice(weighted)
        source = _render(family["tokens"], family["glue_src"])
        translation = _translate(family, scenario, rng)
        pairs.append(Pair(family["name"], scenario, source, translation, family["values"]))
    return pairs
