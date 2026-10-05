"""分层缓存键计算。

缓存键采用"链式键"（与 Docker 经典层缓存语义一致）：

    key_0   = SHA256("layercache-v1\\n" || ""          || NUL || factor(instr_0))
    key_i   = SHA256("layercache-v1\\n" || key_{i-1}   || NUL || factor(instr_i))

factor(instr) 是该指令所有影响产物内容的输入的规范化 JSON：

    FROM    镜像名 + 基础镜像文件树摘要（路径/类型/权限/链接目标/内容 SHA256）
    RUN     规范化后的命令文本
    COPY    目标路径 + 每个源文件条目的描述符
            （路径/类型/权限/链接目标/大小/内容 SHA256）
    ENV     规范化后的赋值文本（K=V ...）
    WORKDIR 规范化后的目录路径
    LABEL   规范化后的键值文本

明确参与：指令文本、源文件内容、源文件权限位 mode、符号链接目标、
基础镜像内容、父层链。
明确不参与：文件 mtime/atime、uid/gid 用户名、文件 inode、缓存生成顺序、
绝对构建机路径之外的宿主机信息。
权限位参与是因为 chmod 会真实改变文件元数据并可能影响运行行为；
属主不参与是因为本模型假设构建对 uid/gid 不敏感（与 Docker 内容寻址
的一致策略相同），而 COPY 时仍会如实写入 uid/gid。
"""

from __future__ import annotations

import hashlib
import json
from typing import Dict, Sequence, Tuple

from .model import FileRecord, Instruction
from .parser import normalize_ctx_path

KEY_SCHEMA_VERSION = b"layercache-v1\n"
_KEY_PREFIX = "layercache:"  # 对外展示时的前缀


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def entry_descriptor(record: FileRecord) -> Dict[str, object]:
    """单个文件条目的缓存描述符。刻意不含 mtime/uid/gid。"""
    desc: Dict[str, object] = {
        "p": record.path,
        "k": record.kind,
        "m": record.mode & 0o7777,
    }
    if record.kind == "file":
        desc["z"] = len(record.content)
        desc["h"] = sha256_hex(record.content)
    elif record.kind == "symlink":
        desc["t"] = record.target
    return desc


def file_tree_digest(records: Sequence[FileRecord]) -> str:
    """一棵文件树的内容摘要：对所有条目按路径排序后逐行哈希。

    同样刻意排除 mtime/uid/gid；mode、链接目标、内容全部纳入。
    """
    hasher = hashlib.sha256()
    hasher.update(b"tree-v1\n")
    for record in sorted(records, key=lambda r: r.path):
        line = json.dumps(
            entry_descriptor(record), sort_keys=True, separators=(",", ":")
        )
        hasher.update(line.encode("utf-8"))
        hasher.update(b"\n")
    return hasher.hexdigest()


def select_tree(records: Sequence[FileRecord], source: str) -> Tuple[FileRecord, ...]:
    """选出路径等于 source 或位于 source/ 之下的全部条目（按路径排序）。"""
    prefix = source if source.endswith("/") else source + "/"
    chosen = [r for r in records if r.path == source or r.path.startswith(prefix)]
    return tuple(sorted(chosen, key=lambda r: r.path))


def _split_copy(arg: str) -> Tuple[Tuple[str, ...], str]:
    tokens = arg.split(" ")
    if len(tokens) < 2:
        raise ValueError(f"COPY 参数不完整: {arg!r}")
    return tuple(tokens[:-1]), tokens[-1]


def copy_factor(arg: str, context: Sequence[FileRecord]) -> Dict[str, object]:
    sources, dest = _split_copy(arg)
    entries = []
    for raw_source in sources:
        source = normalize_ctx_path(raw_source)
        matched = select_tree(context, source)
        if not matched:
            raise ValueError(f"COPY 源在上下文中不存在: {source!r}")
        entries.extend(entry_descriptor(record) for record in matched)
    return {
        "op": "COPY",
        "src": entries,                       # 含路径与内容哈希，顺序稳定
        "dest": dest,
    }


def from_factor(arg: str, base_tree: Sequence[FileRecord]) -> Dict[str, object]:
    return {
        "op": "FROM",
        "image": arg,
        "base_digest": file_tree_digest(base_tree),
    }


def instruction_factor(
    instr: Instruction,
    context: Sequence[FileRecord] = (),
    base_tree: Sequence[FileRecord] = (),
) -> bytes:
    """返回单条指令的规范化 factor 字节串。"""
    if instr.op == "FROM":
        factor = from_factor(instr.arg, base_tree)
    elif instr.op == "COPY":
        factor = copy_factor(instr.arg, context)
    elif instr.op == "RUN":
        factor = {"op": "RUN", "cmd": instr.arg}
    elif instr.op == "ENV":
        factor = {"op": "ENV", "assignments": instr.arg}
    elif instr.op == "WORKDIR":
        factor = {"op": "WORKDIR", "dir": instr.arg}
    elif instr.op == "LABEL":
        factor = {"op": "LABEL", "labels": instr.arg}
    else:  # pragma: no cover - 解析器已拦截
        raise ValueError(f"未知指令: {instr.op}")
    return json.dumps(factor, sort_keys=True, separators=(",", ":")).encode("utf-8")


def chain_key(parent_key: str | None, factor: bytes) -> str:
    """由父层键与本层 factor 计算本层缓存键。"""
    hasher = hashlib.sha256()
    hasher.update(KEY_SCHEMA_VERSION)
    hasher.update((parent_key or "").encode("ascii"))
    hasher.update(b"\x00")
    hasher.update(factor)
    return _KEY_PREFIX + hasher.hexdigest()
