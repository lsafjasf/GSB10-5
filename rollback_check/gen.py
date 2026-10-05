"""新版本 (v2) 数据生成器 —— 构造各场景下“新版本写入”的数据文件。

场景：
- full_compat    v2 保持 v1 的数据与索引格式（完全兼容）
- additive_fields 仅新增可选字段 phone/tags（v1 按 ignore 策略应可读）
- new_enum       10% 记录写入 v2 新枚举值 status="pending"
- index_change   数据兼容，但索引升级为 sharded-hash v2
- id_type_change 主键 id 由 int 变为 str（破坏性变更，演示“不可回滚”）
"""

import json
import os
import random

from .schema import STATUS_V1

SCENARIOS = (
    "full_compat",
    "additive_fields",
    "new_enum",
    "index_change",
    "id_type_change",
)


def _base_record(seq, rng):
    return {
        "id": seq,
        "name": "user%08d" % seq,
        "status": rng.choice(STATUS_V1),
        "age": rng.randint(18, 80),
        "email": "user%08d@example.com" % seq,
    }


def _apply_scenario(rec, scenario, seq):
    if scenario == "additive_fields":
        rec["phone"] = "+86-138-%08d" % (seq % 100000000)
        rec["tags"] = ["vip"] if seq % 7 == 0 else []
    elif scenario == "new_enum":
        if seq % 10 == 0:
            rec["status"] = "pending"  # v2 新增枚举值
    elif scenario == "id_type_change":
        rec["id"] = "u-%08d" % seq  # 主键类型 int -> str
    return rec


def _write_index_v1(path, offsets):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"format": "flat-hash", "version": 1, "entries": offsets}, f)


def _write_index_v2(path, offsets):
    shards = {}
    for key, off in offsets.items():
        shard = str(hash(key) % 16)
        shards.setdefault(shard, {})[key] = off
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"format": "sharded-hash", "version": 2,
                   "shard_bits": 4, "shards": shards}, f)


def write_scenario(directory, scenario, count, seed=42, with_index=True):
    """生成一个场景的数据文件与索引文件，返回 (data_path, index_path)。"""
    if scenario not in SCENARIOS:
        raise ValueError("未知场景: %r" % scenario)
    os.makedirs(directory, exist_ok=True)
    rng = random.Random(seed)
    data_path = os.path.join(directory, scenario + ".jsonl")
    index_path = os.path.join(directory, scenario + ".index.json")

    offsets = {}
    with open(data_path, "w", encoding="utf-8") as f:
        for seq in range(1, count + 1):
            rec = _apply_scenario(_base_record(seq, rng), scenario, seq)
            if with_index:
                offsets[str(rec["id"])] = f.tell()
            f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")))
            f.write("\n")

    if with_index:
        if scenario == "index_change":
            _write_index_v2(index_path, offsets)
        else:
            _write_index_v1(index_path, offsets)
    return data_path, index_path if with_index else None
