"""环境参数文件加载：支持 JSON 对象与 KEY=VALUE 文本（.env 风格）。"""
from __future__ import annotations

import json


def load_env_file(path: str) -> dict[str, str]:
    with open(path, "r", encoding="utf-8") as fh:
        content = fh.read()
    text = content.lstrip("﻿").strip()
    if text.startswith("{"):
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError(f"{path}: JSON 顶层必须是对象")
        return {str(key): _stringify(value) for key, value in data.items()}
    return parse_env_text(content)


def _stringify(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def parse_env_text(content: str) -> dict[str, str]:
    result = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            raise ValueError(f"无法解析的行（缺少 =）：{line!r}")
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip()
    return result
