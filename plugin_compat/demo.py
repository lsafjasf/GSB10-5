"""Print the compatibility decision table for the required sample pairs."""

import copy
import json

from compat_check import check_compatibility
from test_compat_check import base_manifest


def variant(**mutations):
    m = base_manifest("1.0.1")
    for path, value in mutations.items():
        parts = path.split(".")
        node = m
        for p in parts[:-1]:
            node = node[int(p)] if p.isdigit() else node[p]
        node[parts[-1]] = value
    return m


def build_samples():
    base = base_manifest("1.0.0")

    identical = base_manifest("2.0.0")

    optional = base_manifest("1.1.0")
    optional["functions"]["resize_image"]["params"].append(
        {"name": "quality", "kind": "keyword_only", "required": False,
         "default": 90})

    default_changed = base_manifest("1.0.1")
    default_changed["functions"]["resize_image"]["params"][1]["default"] = 1024

    error_changed = base_manifest("1.0.1")
    error_changed["functions"]["resize_image"]["errors"]["bad_dimension"] = \
        "TypeError"

    error_removed = base_manifest("1.0.1")
    del error_removed["functions"]["resize_image"]["errors"]["file_missing"]

    func_removed = base_manifest("2.0.0")
    del func_removed["functions"]["save_image"]

    required_added = base_manifest("2.0.0")
    required_added["functions"]["resize_image"]["params"].append(
        {"name": "mode", "kind": "positional_or_keyword", "required": True})

    return_changed = base_manifest("2.0.0")
    return_changed["functions"]["resize_image"]["returns"] = "bytes"

    return [
        ("完全相同（仅版本号不同）", base, identical),
        ("新增可选参数 quality=90", base, optional),
        ("仅改默认值 width: 800 -> 1024", base, default_changed),
        ("仅改错误语义 bad_dimension: ValueError -> TypeError",
         base, error_changed),
        ("删除错误语义 file_missing", base, error_removed),
        ("删除函数 save_image", base, func_removed),
        ("新增必填参数 mode", base, required_added),
        ("修改返回值 Image -> bytes", base, return_changed),
    ]


def main():
    rows = []
    for label, old, new in build_samples():
        result = check_compatibility(old, new)
        rows.append((label, result))

    verdict_cn = {"compatible": "兼容",
                  "backward_compatible": "向后兼容",
                  "incompatible": "不兼容"}
    print("== 兼容性判定表 ==")
    for label, result in rows:
        print(f"[{verdict_cn[result.verdict]}] {label}")

    print("\n== 不兼容清单 ==")
    for label, result in rows:
        for f in result.incompatibilities:
            print(f"- {label}: {f.code} @ {f.target} -- {f.detail}")

    print("\n== 机器可读输出（仅改默认值样例）==")
    default_result = rows[2][1]
    print(json.dumps(default_result.as_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
