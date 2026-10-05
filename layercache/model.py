"""构建脚本与构建上下文的不可变数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

# 参与缓存键的指令类型
INSTRUCTION_OPS = ("FROM", "RUN", "COPY", "ENV", "WORKDIR", "LABEL")


@dataclass(frozen=True)
class FileRecord:
    """构建上下文（或基础镜像）中的一个文件条目。

    缓存键只使用 path/kind/content/mode/target；
    mtime/uid/gid 属于"无关变化"，不参与缓存键，但会被保留，
    用于证明"忽略它们不会造成错误复用"。
    """

    path: str                      # 规范化绝对路径，如 /src/app.py
    kind: str                      # "file" | "dir" | "symlink"
    content: bytes = b""           # kind=file 时的内容
    mode: int = 0o644              # 权限位（参与缓存键：chmod 必须使缓存失效）
    target: str = ""               # kind=symlink 时的链接目标
    mtime: int = 0                 # 不参与缓存键
    uid: int = 0                   # 不参与缓存键
    gid: int = 0                   # 不参与缓存键


@dataclass(frozen=True)
class Instruction:
    """一条构建指令。arg 为规范化后的参数字符串。"""

    op: str
    arg: str = ""

    def describe(self) -> str:
        return f"{self.op} {self.arg}".rstrip()


@dataclass(frozen=True)
class BuildScript:
    """一组构建脚本：指令序列 + 构建上下文文件。"""

    name: str
    instructions: Tuple[Instruction, ...]
    context: Tuple[FileRecord, ...] = field(default_factory=tuple)

    def instruction_texts(self) -> Tuple[str, ...]:
        return tuple(i.describe() for i in self.instructions)
