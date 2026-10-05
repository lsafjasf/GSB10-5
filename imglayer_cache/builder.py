"""确定性构建器：把 Op 序列逐层应用到一个内存文件系统上。

RUN 支持的命令是一个刻意收窄的确定性 DSL（无网络、无随机源、无外部进程）：

    write <path> <text...>              # 覆盖写文本文件（UTF-8）
    append <path> <text...>             # 追加文本
    mkdir <path> [octal-mode]
    chmod <octal-mode> <path>
    rm <path>                           # 文件/目录/链接
    link <target> <linkpath>            # 创建符号链接
    rename <old> <new>
    touch <path>                        # 只改 mtime（语义无变化）
    chown <user> <path>                 # 只改 uid/gid（语义无变化）

多条子命令可以用 `&&` 连接。touch / chown 的存在就是为了制造“只变
时间戳 / 所有者”的构建脚本，验证缓存不应失效。
"""
from __future__ import annotations

import posixpath
import shlex
import zlib
from dataclasses import dataclass, field

from .hashing import (
    copy_op_digest,
    diff_digest,
    env_op_digest,
    layer_key,
    root_key,
    run_op_digest,
    snapshot_digest,
    workdir_op_digest,
)
from .model import BuildContext, FileRecord, Op


SEMANTIC_FIELDS = ("kind", "mode", "target", "content")


def semantic_equal(a: FileRecord | None, b: FileRecord | None) -> bool:
    """两个节点在语义上是否相等（忽略 uid/gid/mtime）。"""
    if a is None or b is None:
        return a is b
    return all(getattr(a, name) == getattr(b, name) for name in SEMANTIC_FIELDS)


class FileSystem:
    """以 dict 表示的内存文件系统，路径为规范化的绝对 POSIX 路径。"""

    def __init__(self, files: dict[str, FileRecord] | None = None, clock: int = 0):
        self.files: dict[str, FileRecord] = dict(files or {})
        self.clock = clock

    @staticmethod
    def normalize(path: str, workdir: str) -> str:
        if not path.startswith("/"):
            path = posixpath.join(workdir, path)
        return posixpath.normpath(path)

    def tick(self) -> int:
        self.clock += 1
        return self.clock

    def _record_change(
        self, changes: dict[str, FileRecord | None], path: str, record: FileRecord
    ) -> None:
        before = changes.get(path, _MISSING)
        old = before if before is not _MISSING else self.files.get(path)
        if not semantic_equal(old, record):
            changes[path] = record

    def _ensure_parents(self, path: str, changes: dict[str, FileRecord | None]) -> None:
        parent = posixpath.dirname(path)
        created: list[str] = []
        while parent not in ("/", "") and parent not in self.files:
            created.append(parent)
            parent = posixpath.dirname(parent)
        for directory in reversed(created):
            record = FileRecord(kind="dir", mode=0o755, mtime_ns=self.tick())
            self._record_change(changes, directory, record)
            self.files[directory] = record

    def put(
        self,
        path: str,
        record: FileRecord,
        changes: dict[str, FileRecord | None],
    ) -> None:
        self._ensure_parents(path, changes)
        self._record_change(changes, path, record)
        self.files[path] = record

    def remove(self, path: str, changes: dict[str, FileRecord | None]) -> None:
        if path in self.files:
            changes[path] = None
            del self.files[path]

    def apply_change(self, path: str, record: FileRecord | None) -> None:
        """按缓存层差异重放（缓存命中路径使用）。"""
        if record is None:
            self.files.pop(path, None)
        else:
            self.files[path] = record

    # -- COPY --------------------------------------------------------------
    def copy_in(
        self,
        src: str,
        dst: str,
        mode: int | None,
        context: BuildContext,
        workdir: str,
        changes: dict[str, FileRecord | None],
    ) -> None:
        key = src.lstrip("./")
        if key not in context.files:
            raise KeyError(f"构建上下文中缺少 {src!r}")
        source = context.files[key]
        target_path = self.normalize(dst, workdir)
        effective_mode = mode if mode is not None else source.mode
        record = FileRecord(
            content=source.content,
            mode=effective_mode,
            kind=source.kind,
            target=source.target,
            mtime_ns=self.tick(),
        )
        self.put(target_path, record, changes)

    # -- RUN DSL -----------------------------------------------------------
    def run_dsl(
        self,
        command: str,
        workdir: str,
        env: dict[str, str],
        changes: dict[str, FileRecord | None],
    ) -> None:
        tokens = shlex.split(command)
        for segment in _split_on_and(tokens):
            self._run_one(segment, workdir, env, changes)

    def _run_one(
        self,
        tokens: list[str],
        workdir: str,
        env: dict[str, str],
        changes: dict[str, FileRecord | None],
    ) -> None:
        if not tokens:
            return
        name = tokens[0]
        args = tokens[1:]
        if name in ("write", "append") and len(args) >= 2:
            path = self.normalize(args[0], workdir)
            text = " ".join(args[1:]).encode("utf-8")
            old = self.files.get(path)
            if name == "append" and old is not None:
                text = old.content + text
            mode = old.mode if old is not None else 0o644
            self.put(
                path,
                FileRecord(kind="file", mode=mode, content=text, mtime_ns=self.tick()),
                changes,
            )
        elif name == "mkdir" and len(args) >= 1:
            path = self.normalize(args[0], workdir)
            mode = int(args[1], 8) if len(args) >= 2 else 0o755
            if path not in self.files:
                self.put(
                    path,
                    FileRecord(kind="dir", mode=mode, mtime_ns=self.tick()),
                    changes,
                )
        elif name == "chmod" and len(args) >= 2:
            path = self.normalize(args[1], workdir)
            old = self.files[path]
            self.put(
                path,
                FileRecord(
                    content=old.content,
                    mode=int(args[0], 8),
                    kind=old.kind,
                    target=old.target,
                    mtime_ns=self.tick(),
                ),
                changes,
            )
        elif name == "rm" and len(args) == 1:
            self.remove(self.normalize(args[0], workdir), changes)
        elif name == "link" and len(args) == 2:
            path = self.normalize(args[1], workdir)
            self.put(
                path,
                FileRecord(kind="symlink", mode=0o777, target=args[0].encode(), mtime_ns=self.tick()),
                changes,
            )
        elif name == "rename" and len(args) == 2:
            old_path = self.normalize(args[0], workdir)
            new_path = self.normalize(args[1], workdir)
            old = self.files[old_path]
            self.remove(old_path, changes)
            self.put(
                new_path,
                FileRecord(
                    content=old.content,
                    mode=old.mode,
                    kind=old.kind,
                    target=old.target,
                    mtime_ns=self.tick(),
                ),
                changes,
            )
        elif name == "touch" and len(args) == 1:
            path = self.normalize(args[0], workdir)
            old = self.files[path]
            self.files[path] = FileRecord(
                content=old.content,
                mode=old.mode,
                kind=old.kind,
                target=old.target,
                uid=old.uid,
                gid=old.gid,
                mtime_ns=self.tick(),
            )
        elif name == "chown" and len(args) == 2:
            path = self.normalize(args[1], workdir)
            old = self.files[path]
            ident = zlib.crc32(args[0].encode()) & 0xFFFFFFFF
            self.files[path] = FileRecord(
                content=old.content,
                mode=old.mode,
                kind=old.kind,
                target=old.target,
                uid=ident,
                gid=ident,
                mtime_ns=old.mtime_ns,
            )
        else:
            raise ValueError(f"不支持或参数错误的 RUN 子命令: {shlex.join(tokens)}")


_MISSING = object()


def _split_on_and(tokens: list[str]):
    current: list[str] = []
    for token in tokens:
        if token == "&&":
            yield current
            current = []
        else:
            current.append(token)
    yield current


@dataclass
class BuildLayer:
    index: int
    op: Op
    key: str
    diff_digest_value: str
    changes: dict[str, FileRecord | None]
    snapshot_before: str
    snapshot_after: str


@dataclass
class BuildResult:
    base_image: str
    rootfs_digest: str
    layers: list[BuildLayer] = field(default_factory=list)
    files: dict[str, FileRecord] = field(default_factory=dict)
    workdir: str = "/"
    env: dict[str, str] = field(default_factory=dict)
    final_snapshot: str = ""


def build(
    script: list[Op],
    context: BuildContext,
    base_rootfs: dict[str, FileRecord] | None = None,
    clock_start: int = 0,
) -> BuildResult:
    """从零构建，返回逐层结果（每层都带键、差异摘要、构建前后快照摘要）。"""
    if not script or script[0].kind != "from":
        raise ValueError("脚本必须以 FROM 开头")
    base_image = script[0].args[0]
    rootfs = dict(base_rootfs or {})
    rootfs_digest_value = snapshot_digest(rootfs)

    fs = FileSystem(rootfs, clock=clock_start)
    result = BuildResult(
        base_image=base_image,
        rootfs_digest=rootfs_digest_value,
        files=fs.files,
    )
    parent = root_key(base_image, rootfs_digest_value)

    for index, op in enumerate(script[1:], start=1):
        changes: dict[str, FileRecord | None] = {}
        snapshot_before = snapshot_digest(fs.files)
        if op.kind == "copy":
            src, dst, mode = op.args
            fs.copy_in(src, dst, mode, context, result.workdir, changes)
            source = context.files[src.lstrip("./")]
            op_digest_value = copy_op_digest(src, dst, mode, source)
        elif op.kind == "run":
            (command,) = op.args
            fs.run_dsl(command, result.workdir, result.env, changes)
            op_digest_value = run_op_digest(command, result.workdir, result.env)
        elif op.kind == "workdir":
            (raw_path,) = op.args
            result.workdir = fs.normalize(raw_path, result.workdir)
            op_digest_value = workdir_op_digest(result.workdir)
        elif op.kind == "env":
            key, value = op.args
            result.env = dict(result.env)
            result.env[key] = value
            op_digest_value = env_op_digest(key, value)
        else:  # pragma: no cover - parse_script 已拦截
            raise ValueError(f"未知指令 {op.kind}")

        key = layer_key(parent, op_digest_value)
        parent = key
        result.layers.append(
            BuildLayer(
                index=index,
                op=op,
                key=key,
                diff_digest_value=diff_digest(changes),
                changes=changes,
                snapshot_before=snapshot_before,
                snapshot_after=snapshot_digest(fs.files),
            )
        )

    result.final_snapshot = snapshot_digest(fs.files)
    return result
