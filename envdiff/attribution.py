"""差异归因来源：声明的期望值、允许差异白名单、历史基线。

三种归因来源的优先级（高 -> 低）：
1. whitelist  允许差异白名单：显式声明"这个差异是允许的"，可限定两边各自的期望值；
2. expected   声明的期望值：声明某参数在两个环境各自应该是什么 / 默认值是什么；
3. baseline   历史基线：上一版本（已知正确环境）的参数快照，用于发现漂移与遗漏。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .normalize import canonical

MISSING = object()


@dataclass
class WhitelistRule:
    """一条白名单规则。key 精确匹配或 pattern 正则匹配；
    expect_a / expect_b 可选，限定两边各自必须等于的期望值。"""

    reason: str
    key: str | None = None
    pattern: re.Pattern | None = None
    expect_a: object | None = None
    expect_b: object | None = None
    allow_missing: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> "WhitelistRule":
        pattern = data.get("pattern")
        return cls(
            reason=data.get("reason", "白名单允许"),
            key=data.get("key"),
            pattern=re.compile(pattern) if pattern else None,
            expect_a=data.get("env_a"),
            expect_b=data.get("env_b"),
            allow_missing=data.get("allow_missing", True),
        )

    def matches_key(self, key: str) -> bool:
        if self.key is not None:
            return self.key == key
        if self.pattern is not None:
            return bool(self.pattern.search(key))
        return False

    def matches(self, key: str, value_a, value_b) -> bool:
        if not self.matches_key(key):
            return False
        if value_a is not MISSING and self.expect_a is not None \
                and canonical(value_a) != canonical(self.expect_a):
            return False
        if value_b is not MISSING and self.expect_b is not None \
                and canonical(value_b) != canonical(self.expect_b):
            return False
        return True


class ExpectedSpec:
    """声明的期望值。JSON 格式：
    {"KEY": {"env_a": "...", "env_b": "..."}}  -> 两环境各自应有值
    {"KEY": {"default": "..."}}                -> 默认值（用于识别"调优未应用"）
    {"KEY": {}}                                -> 仅声明该参数应存在
    """

    def __init__(self, data: dict | None):
        self._data = data or {}

    def declares(self, key: str) -> bool:
        return key in self._data

    def declared_keys(self):
        return self._data.keys()

    def default_for(self, key: str):
        return self._data.get(key, {}).get("default", MISSING)

    def matches_envs(self, key: str, value_a, value_b) -> bool:
        entry = self._data.get(key)
        if not isinstance(entry, dict):
            return False
        expect_a, expect_b = entry.get("env_a", MISSING), entry.get("env_b", MISSING)
        if expect_a is MISSING and expect_b is MISSING:
            return False
        if expect_a is not MISSING and canonical(value_a) != canonical(expect_a):
            return False
        if expect_b is not MISSING and canonical(value_b) != canonical(expect_b):
            return False
        return True


def load_json_file(path: str):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)
