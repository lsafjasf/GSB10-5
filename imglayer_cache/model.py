"""数据模型：构建脚本指令（Op）、构建上下文（BuildContext）、文件记录（FileRecord）。

构建脚本是一个极小的、确定性的 Dockerfile 子集：

    FROM <image>
    COPY <context-src> <image-dst> [octal-mode]
    RUN <command>                     # 见 builder.py 中支持的确定性 DSL
    WORKDIR <path>
    ENV <key> <value>
"""
from __future__ import annotations

from dataclasses import dataclass
import shlex


@dataclass(frozen=True)
class FileRecord:
    """镜像/上下文中的一个文件系统节点。

    参与“语义”的字段：kind、mode、target、content。
    不参与语义的字段：uid、gid、mtime_ns —— 所有者与时间戳变化既不得使
    缓存失效，也不允许因为忽略它们而把真正不同的内容判成相同（mode/content
    等语义字段仍然独立参与哈希）。
    """

    content: bytes = b""
    mode: int = 0o644
    kind: str = "file"            # file | dir | symlink
    target: bytes | None = None   # 仅 kind == "symlink" 时有效
    uid: int = 0
    gid: int = 0
    mtime_ns: int = 0


@dataclass(frozen=True)
class Op:
    """一条构建指令。kind 见模块文档；args 为结构化参数，raw 为原始文本。"""

    kind: str
    args: tuple
    raw: str
    lineno: int = 0


@dataclass
class BuildContext:
    """构建上下文：相对路径 -> FileRecord（类似 docker build 的上下文目录）。"""

    files: dict[str, FileRecord]


def parse_script(text: str) -> list[Op]:
    """把构建脚本文本解析成 Op 列表。空行与 # 注释行会被忽略。"""
    ops: list[Op] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = shlex.split(stripped)
        head = parts[0].lower()
        if head == "from":
            if len(parts) != 2:
                raise ValueError(f"line {lineno}: FROM 需要恰好一个参数")
            ops.append(Op("from", (parts[1],), stripped, lineno))
        elif head == "copy":
            if len(parts) < 3:
                raise ValueError(f"line {lineno}: COPY 需要 SRC 与 DST")
            mode = int(parts[3], 8) if len(parts) >= 4 else None
            ops.append(Op("copy", (parts[1], parts[2], mode), stripped, lineno))
        elif head == "run":
            command = stripped.split(None, 1)[1] if len(parts) > 1 else ""
            ops.append(Op("run", (command,), stripped, lineno))
        elif head == "workdir":
            if len(parts) != 2:
                raise ValueError(f"line {lineno}: WORKDIR 需要恰好一个参数")
            ops.append(Op("workdir", (parts[1],), stripped, lineno))
        elif head == "env":
            if len(parts) < 3:
                raise ValueError(f"line {lineno}: ENV 需要 KEY 与 VALUE")
            ops.append(Op("env", (parts[1], " ".join(parts[2:])), stripped, lineno))
        else:
            raise ValueError(f"line {lineno}: 不支持的指令 {parts[0]!r}")
    return ops
