"""已发布码稳定性校验。

已发布（进入 release 快照）的错误码是前端与第三方依赖的契约，禁止：
- 重新赋义：码不变但错误名变化（前端按码处理的逻辑会静默错位）— 严重；
- 文案漂移：名未变但默认文案含义变化 — 警告；
- 直接删除：已发布码消失 — 警告（应走废弃流程，保留码位）。

新增码不会报错。快照为 JSON：
    {"version": "...", "codes": {"E0101001": {"name": ..., "default_message": ...}}}
"""

import json


def snapshot_from_registry(registry, version, released_at=None):
    codes = {}
    for code, entry in registry.entries.items():
        definition = entry["def"]
        codes[code] = {
            "name": definition.get("name"),
            "default_message": definition.get("default_message"),
            "module": definition.get("module"),
        }
    return {"version": version, "released_at": released_at, "codes": codes}


def save_snapshot(snapshot, path):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, ensure_ascii=False, indent=2, sort_keys=True)


def load_snapshot(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def check_stability(registry, snapshot):
    """当前定义与已发布快照对比。"""
    old = snapshot.get("codes", {})
    new = registry.entries

    added, repurposed, message_drift, removed = [], [], [], []
    for code, entry in new.items():
        definition = entry["def"]
        if code not in old:
            added.append({"code": code, "name": definition.get("name")})
            continue
        before, after = old[code], definition
        if before.get("name") != after.get("name"):
            repurposed.append({
                "code": code,
                "module": after.get("module"),
                "old_name": before.get("name"),
                "new_name": after.get("name"),
            })
        elif before.get("default_message") != after.get("default_message"):
            message_drift.append({
                "code": code,
                "module": after.get("module"),
                "old_message": before.get("default_message"),
                "new_message": after.get("default_message"),
            })

    for code, before in old.items():
        if code not in new:
            removed.append({"code": code, "name": before.get("name")})

    return {
        "baseline_version": snapshot.get("version"),
        "released_count": len(old),
        "current_count": len(new),
        "added_count": len(added),
        "removed_count": len(removed),
        "repurposed_count": len(repurposed),
        "message_drift_count": len(message_drift),
        "has_breaking_changes": bool(repurposed),
        "added": sorted(added, key=lambda x: x["code"]),
        "removed": sorted(removed, key=lambda x: x["code"]),
        "repurposed": sorted(repurposed, key=lambda x: x["code"]),
        "message_drift": sorted(message_drift, key=lambda x: x["code"]),
    }
