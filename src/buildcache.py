"""构建缓存键库（Python 3 标准库）。

键 = SHA-256，参与计算的五组因素：
  1. source    源码：相对路径 + 文件内容哈希（绝不包含绝对根目录）
  2. toolchain 工具链：编译器/解释器名称与版本
  3. params    参数：影响产物的构建参数（规范化后参与）
  4. env       环境：显式登记（allowlist）的环境变量
  5. platform  平台：OS / CPU 架构 / libc

设计取舍见 README。
"""

from __future__ import annotations

import hashlib
import os
import platform
from dataclasses import dataclass, field
from typing import Mapping

KEY_VERSION = 1

# 默认参与键计算的环境变量：只收"能改变产物"的变量。
# 采用 allowlist（默认不纳入），而不是 denylist：
# PWD/SHLVL/_ 等无关变量不进入键，避免缓存永远 miss；
# 新增工具时若引入新的环境依赖，必须显式登记，避免静默错误命中。
DEFAULT_ENV_ALLOWLIST = (
    "CC", "CXX", "AR", "LD",
    "CFLAGS", "CXXFLAGS", "LDFLAGS", "CPPFLAGS",
    "LANG", "LC_ALL",
    "TARGET", "SYSROOT",
)


@dataclass(frozen=True)
class BuildInputs:
    sources: Mapping[str, bytes]          # 相对路径 -> 文件内容
    toolchain: Mapping[str, str]          # 工具名 -> 版本，如 {"gcc": "13.2.0"}
    params: Mapping[str, str]             # 构建参数，如 {"opt": "-O2"}
    env: Mapping[str, str] = field(default_factory=dict)
    platform_info: Mapping[str, str] = field(default_factory=dict)

    @staticmethod
    def detect_platform() -> dict[str, str]:
        libc = " ".join(platform.libc_ver()) if platform.libc_ver()[0] else "unknown"
        return {
            "os": platform.system(),
            "arch": platform.machine(),
            "libc": libc,
        }


def _frame(tag: bytes, payload: bytes, h: "hashlib._Hash") -> None:
    h.update(tag)
    h.update(len(payload).to_bytes(4, "big"))
    h.update(payload)


def _canonical_map(m: Mapping) -> bytes:
    # 长度前缀 + 排序后的 kv，避免 "a=b/c=d" 这类拼接歧义
    parts = []
    for k in sorted(m):
        kk = str(k).encode("utf-8")
        vv = str(m[k]).encode("utf-8")
        parts.append(len(kk).to_bytes(2, "big") + kk +
                     len(vv).to_bytes(4, "big") + vv)
    body = b"".join(parts)
    return len(m).to_bytes(2, "big") + body


def _canonical_sources(sources: Mapping[str, bytes]) -> bytes:
    entries = []
    for rel in sorted(sources):
        if os.path.isabs(rel) or rel.startswith(".."):
            raise ValueError(f"源码路径必须是相对路径且不得逃逸: {rel!r}")
        content_hash = hashlib.sha256(sources[rel]).digest()
        rb = rel.encode("utf-8")
        entries.append(len(rb).to_bytes(2, "big") + rb + content_hash)
    body = b"".join(entries)
    return len(sources).to_bytes(4, "big") + body


def compute_key(inputs: BuildInputs, *, include_env: bool = True) -> str:
    """返回十六进制缓存键。include_env=False 仅用于演示"少算环境因素"的错误键。"""
    h = hashlib.sha256()
    _frame(b"keyver", str(KEY_VERSION).encode(), h)
    _frame(b"source", _canonical_sources(inputs.sources), h)
    _frame(b"toolchain", _canonical_map(inputs.toolchain), h)
    _frame(b"params", _canonical_map(inputs.params), h)
    if include_env:
        _frame(b"env", _canonical_map(inputs.env), h)
    _frame(b"platform", _canonical_map(inputs.platform_info), h)
    return h.hexdigest()


def select_env(full_env: Mapping[str, str],
               allowlist=DEFAULT_ENV_ALLOWLIST) -> dict[str, str]:
    """从完整环境中挑出 allowlist 内、非空的变量，供 BuildInputs.env 使用。"""
    return {k: full_env[k] for k in allowlist if full_env.get(k)}


def lookup(cache: Mapping[str, object], inputs: BuildInputs, *,
           include_env: bool = True) -> tuple[str, str]:
    """返回 (key, 判定)；判定为 HIT / MISS。"""
    key = compute_key(inputs, include_env=include_env)
    return key, ("HIT" if key in cache else "MISS")
