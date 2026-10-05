"""模拟构建器：在内存文件系统上执行构建脚本，并接入层缓存。

执行模型刻意保持小而确定：
- 文件系统是 dict[path, Node]，Node 记录 kind/content/mode/target/mtime/uid/gid。
- RUN 支持一个受控的迷你命令集（write/append/mkdir/rm/mv/chmod/chown/ln/touch），
  用于在自测中构造确定性的文件系统变化。
- 层内容 = 该层对文件系统的增删改集合（删除用 whiteout 表示）。
- 层内容摘要与镜像摘要都排除 mtime/uid/gid，与缓存键策略一致。
"""

from __future__ import annotations

import hashlib
import json
import shlex
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .keys import chain_key, file_tree_digest, instruction_factor, select_tree
from .model import BuildScript, FileRecord, Instruction
from .parser import normalize_ctx_path

# ---------------------------------------------------------------- 文件系统


@dataclass
class Node:
    kind: str                       # "file" | "dir" | "symlink"
    content: bytes = b""
    mode: int = 0o644
    target: str = ""
    mtime: int = 0
    uid: int = 0
    gid: int = 0

    def clone(self) -> "Node":
        return Node(self.kind, self.content, self.mode, self.target,
                    self.mtime, self.uid, self.gid)


def _norm(path: str, cwd: str = "/") -> str:
    if not path.startswith("/"):
        path = cwd.rstrip("/") + "/" + path
    parts: List[str] = []
    for seg in path.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
            continue
        parts.append(seg)
    return "/" + "/".join(parts)


def _ensure_dirs(fs: Dict[str, Node], path: str) -> None:
    parts = [p for p in path.split("/") if p]
    cur = ""
    for seg in parts:
        cur += "/" + seg
        if cur not in fs:
            fs[cur] = Node(kind="dir", mode=0o755)


def _remove_tree(fs: Dict[str, Node], path: str) -> None:
    prefix = path + "/"
    for key in [k for k in fs if k == path or k.startswith(prefix)]:
        del fs[key]


def _tree_records(fs: Dict[str, Node]) -> Tuple[FileRecord, ...]:
    return tuple(
        FileRecord(path=p, kind=n.kind, content=n.content, mode=n.mode,
                   target=n.target, mtime=n.mtime, uid=n.uid, gid=n.gid)
        for p, n in sorted(fs.items())
    )


def tree_digest(fs: Dict[str, Node]) -> str:
    return file_tree_digest(_tree_records(fs))


# ---------------------------------------------------------------- 基础镜像

def _base_tree(files: Sequence[Tuple[str, str, int]]) -> Tuple[FileRecord, ...]:
    records: List[FileRecord] = []
    dirs = set()
    for path, content, mode in files:
        parts = [p for p in path.split("/") if p]
        cur = ""
        for seg in parts[:-1]:
            cur += "/" + seg
            dirs.add(cur)
        records.append(FileRecord(path=path, kind="file",
                                  content=content.encode(), mode=mode))
    for d in sorted(dirs):
        records.append(FileRecord(path=d, kind="dir", mode=0o755))
    return tuple(sorted(records, key=lambda r: r.path))


BASE_IMAGES: Dict[str, Tuple[FileRecord, ...]] = {
    "ubuntu:22.04": _base_tree([
        ("/etc/os-release", 'NAME="Ubuntu"\nVERSION="22.04 LTS"\n', 0o644),
        ("/usr/bin/env", "#!/bin/sh\n", 0o755),
        ("/var/lib/dpkg/status", "Package: base-files\n", 0o644),
    ]),
    "alpine:3.19": _base_tree([
        ("/etc/os-release", 'NAME="Alpine Linux"\nVERSION_ID=3.19\n', 0o644),
        ("/bin/sh", "#!/bin/busybox\n", 0o755),
    ]),
}


# ---------------------------------------------------------------- 层与缓存

@dataclass(frozen=True)
class FileChange:
    """层内一次文件系统变化。kind="whiteout" 表示删除。"""
    path: str
    kind: str                       # file | dir | symlink | whiteout
    content: bytes = b""
    mode: int = 0
    target: str = ""
    mtime: int = 0
    uid: int = 0
    gid: int = 0


def changeset_digest(changes: Sequence[FileChange]) -> str:
    """层内容摘要：规范化、排序、排除 mtime/uid/gid。"""
    hasher = hashlib.sha256()
    hasher.update(b"changeset-v1\n")
    for change in sorted(changes, key=lambda c: c.path):
        entry: Dict[str, object] = {"p": change.path, "k": change.kind}
        if change.kind == "file":
            entry["m"] = change.mode & 0o7777
            entry["z"] = len(change.content)
            entry["h"] = hashlib.sha256(change.content).hexdigest()
        elif change.kind == "dir":
            entry["m"] = change.mode & 0o7777
        elif change.kind == "symlink":
            entry["t"] = change.target
        hasher.update(json.dumps(entry, sort_keys=True,
                                 separators=(",", ":")).encode())
        hasher.update(b"\n")
    return hasher.hexdigest()


def changeset_size(changes: Sequence[FileChange]) -> int:
    return sum(len(c.content) for c in changes if c.kind == "file")


@dataclass
class Layer:
    index: int
    instruction: str
    cache_key: str
    content_digest: str
    size_bytes: int
    changes: Tuple[FileChange, ...]
    image_digest: str               # 应用本层后整棵文件树的摘要
    reused: bool = False


@dataclass
class Artifact:
    """缓存条目：键 -> 该层产物（变化集 + 执行后镜像摘要）。"""
    key: str
    content_digest: str
    changes: Tuple[FileChange, ...]
    image_digest: str
    size_bytes: int


class CacheStore:
    def __init__(self) -> None:
        self._entries: Dict[str, Artifact] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[Artifact]:
        artifact = self._entries.get(key)
        if artifact is not None:
            self.hits += 1
        else:
            self.misses += 1
        return artifact

    def put(self, artifact: Artifact) -> None:
        self._entries[artifact.key] = artifact

    def __len__(self) -> int:
        return len(self._entries)

    # 仅用于自测：模拟损坏的缓存条目
    def corrupt(self, key: str) -> None:
        artifact = self._entries[key]
        self._entries[key] = Artifact(
            key=artifact.key,
            content_digest="0" * 64,
            changes=artifact.changes,
            image_digest=artifact.image_digest,
            size_bytes=artifact.size_bytes,
        )


@dataclass
class BuildResult:
    name: str
    layers: List[Layer]
    image_digest: str
    fs: Dict[str, Node] = field(repr=False, default_factory=dict)

    @property
    def reused_count(self) -> int:
        return sum(1 for layer in self.layers if layer.reused)


# ---------------------------------------------------------------- RUN 迷你命令集

def _expand(token: str, env: Dict[str, str]) -> str:
    out = token
    for key, value in env.items():
        out = out.replace("${" + key + "}", value).replace("$" + key, value)
    return out


def _run_one(fs: Dict[str, Node], tokens: List[str], cwd: str,
             env: Dict[str, str]) -> str:
    cmd = tokens[0]
    args = [_expand(t, env) for t in tokens[1:]]
    if cmd == "write":                      # write PATH TEXT...
        path, text = _norm(args[0], cwd), (" ".join(args[1:]) + "\n").encode()
        _ensure_dirs(fs, path.rsplit("/", 1)[0] or "/")
        fs[path] = Node(kind="file", content=text, mode=0o644)
    elif cmd == "append":                   # append PATH TEXT...
        path, text = _norm(args[0], cwd), (" ".join(args[1:]) + "\n").encode()
        node = fs.get(path)
        if node is None:
            raise ValueError(f"append: 文件不存在 {path}")
        node.content += text
    elif cmd == "mkdir":                    # mkdir [-p] PATH
        target = _norm(args[-1], cwd)
        _ensure_dirs(fs, target)
    elif cmd == "rm":                       # rm [-r|-rf] PATH
        target = _norm(args[-1], cwd)
        if target not in fs:
            raise ValueError(f"rm: 不存在 {target}")
        _remove_tree(fs, target)
    elif cmd == "mv":                       # mv SRC DST
        src, dst = _norm(args[0], cwd), _norm(args[1], cwd)
        if src not in fs:
            raise ValueError(f"mv: 不存在 {src}")
        node = fs[src]
        _remove_tree(fs, src)
        _ensure_dirs(fs, dst.rsplit("/", 1)[0] or "/")
        fs[dst] = node
    elif cmd == "chmod":                    # chmod MODE PATH
        node = fs.get(_norm(args[1], cwd))
        if node is None:
            raise ValueError(f"chmod: 不存在 {args[1]}")
        node.mode = int(args[0], 8)
    elif cmd == "chown":                    # chown UID:GID PATH
        uid_s, _, gid_s = args[0].partition(":")
        node = fs.get(_norm(args[1], cwd))
        if node is None:
            raise ValueError(f"chown: 不存在 {args[1]}")
        node.uid, node.gid = int(uid_s), int(gid_s or uid_s)
    elif cmd == "ln":                       # ln -s TARGET LINK
        if args[0] != "-s":
            raise ValueError("ln 仅支持 -s")
        target, link = args[1], _norm(args[2], cwd)
        _ensure_dirs(fs, link.rsplit("/", 1)[0] or "/")
        fs[link] = Node(kind="symlink", target=target, mode=0o777)
    elif cmd == "touch":                    # touch PATH（只改 mtime）
        node = fs.get(_norm(args[0], cwd))
        if node is None:
            raise ValueError(f"touch: 不存在 {args[0]}")
        node.mtime += 1
    else:
        raise ValueError(f"RUN 迷你命令集不支持: {cmd!r}")
    return cwd


def run_command(fs: Dict[str, Node], command: str, cwd: str,
                env: Dict[str, str]) -> str:
    for segment in command.split("&&"):
        tokens = shlex.split(segment.strip())
        if tokens:
            cwd = _run_one(fs, tokens, cwd, env)
    return cwd


# ---------------------------------------------------------------- 指令执行

def _snapshot(fs: Dict[str, Node]) -> Dict[str, Node]:
    return {p: n.clone() for p, n in fs.items()}


def _diff(before: Dict[str, Node], after: Dict[str, Node]) -> Tuple[FileChange, ...]:
    changes: List[FileChange] = []
    for path, node in after.items():
        old = before.get(path)
        if old is None or vars(old) != vars(node):
            changes.append(FileChange(
                path=path, kind=node.kind, content=node.content,
                mode=node.mode, target=node.target,
                mtime=node.mtime, uid=node.uid, gid=node.gid))
    for path in before:
        if path not in after:
            changes.append(FileChange(path=path, kind="whiteout"))
    return tuple(sorted(changes, key=lambda c: c.path))


def _apply_changes(fs: Dict[str, Node], changes: Sequence[FileChange]) -> None:
    for change in changes:
        if change.kind == "whiteout":
            _remove_tree(fs, change.path)
        else:
            parent = change.path.rsplit("/", 1)[0] or "/"
            if change.kind != "dir":
                _ensure_dirs(fs, parent)
            fs[change.path] = Node(kind=change.kind, content=change.content,
                                   mode=change.mode, target=change.target,
                                   mtime=change.mtime, uid=change.uid,
                                   gid=change.gid)


def _execute(instr: Instruction, fs: Dict[str, Node], state: Dict[str, object],
             script: BuildScript) -> None:
    cwd = state["cwd"]  # type: ignore[index]
    env: Dict[str, str] = state["env"]  # type: ignore[assignment]
    if instr.op == "FROM":
        if instr.arg not in BASE_IMAGES:
            raise ValueError(f"未知基础镜像: {instr.arg!r}")
        fs.clear()
        for record in BASE_IMAGES[instr.arg]:
            fs[record.path] = Node(kind=record.kind, content=record.content,
                                   mode=record.mode, target=record.target,
                                   mtime=record.mtime, uid=record.uid,
                                   gid=record.gid)
    elif instr.op == "RUN":
        state["cwd"] = run_command(fs, instr.arg, cwd, env)
    elif instr.op == "COPY":
        tokens = instr.arg.split(" ")
        raw_dest = tokens[-1]
        dest_is_dir = raw_dest.endswith("/") or len(tokens) > 2
        dest = _norm(raw_dest.rstrip("/"), cwd)
        for raw_source in tokens[:-1]:
            source = normalize_ctx_path(raw_source)
            matched = select_tree(script.context, source)
            if not matched:
                raise ValueError(f"COPY 源不存在: {source!r}")
            single_file = (len(matched) == 1
                           and matched[0].kind == "file"
                           and matched[0].path == source)
            for record in matched:
                if single_file and not dest_is_dir:
                    target = dest
                else:
                    rel = record.path[len(source):].lstrip("/") \
                        if record.path != source else record.path.rsplit("/", 1)[-1]
                    target = _norm(dest + "/" + rel)
                _ensure_dirs(fs, target.rsplit("/", 1)[0] or "/")
                fs[target] = Node(kind=record.kind, content=record.content,
                                  mode=record.mode, target=record.target,
                                  mtime=record.mtime, uid=record.uid,
                                  gid=record.gid)
    elif instr.op == "ENV":
        for token in instr.arg.split(" "):
            key, _, value = token.partition("=")
            env[key] = value
    elif instr.op == "WORKDIR":
        state["cwd"] = _norm(instr.arg, cwd)
        _ensure_dirs(fs, state["cwd"])  # type: ignore[arg-type]
    elif instr.op == "LABEL":
        pass
    else:  # pragma: no cover
        raise ValueError(f"未知指令: {instr.op}")


# ---------------------------------------------------------------- 构建入口

def run_build(script: BuildScript, cache: Optional[CacheStore] = None,
              base_images: Optional[Dict[str, Tuple[FileRecord, ...]]] = None
              ) -> BuildResult:
    bases = base_images if base_images is not None else BASE_IMAGES
    fs: Dict[str, Node] = {}
    state: Dict[str, object] = {"cwd": "/", "env": {}}
    layers: List[Layer] = []
    parent_key: Optional[str] = None

    for index, instr in enumerate(script.instructions):
        base_tree: Tuple[FileRecord, ...] = ()
        if instr.op == "FROM":
            if instr.arg not in bases:
                raise ValueError(f"未知基础镜像: {instr.arg!r}")
            base_tree = bases[instr.arg]
        factor = instruction_factor(instr, script.context, base_tree)
        key = chain_key(parent_key, factor)

        if instr.op == "FROM":
            state["cwd"] = "/"
            state["env"] = {}

        artifact = cache.get(key) if cache is not None else None
        if artifact is not None:
            _apply_changes(fs, artifact.changes)
            if instr.op == "ENV":
                for token in instr.arg.split(" "):
                    name, _, value = token.partition("=")
                    state["env"][name] = value  # type: ignore[index]
            elif instr.op == "WORKDIR":
                state["cwd"] = _norm(instr.arg, state["cwd"])  # type: ignore[arg-type]
            layer = Layer(index=index, instruction=instr.describe(),
                          cache_key=key, content_digest=artifact.content_digest,
                          size_bytes=artifact.size_bytes,
                          changes=artifact.changes,
                          image_digest=artifact.image_digest, reused=True)
        else:
            before = _snapshot(fs)
            _execute(instr, fs, state, script)
            changes = _diff(before, fs)
            digest = changeset_digest(changes)
            image_digest = tree_digest(fs)
            layer = Layer(index=index, instruction=instr.describe(),
                          cache_key=key, content_digest=digest,
                          size_bytes=changeset_size(changes),
                          changes=changes, image_digest=image_digest,
                          reused=False)
            if cache is not None:
                cache.put(Artifact(key=key, content_digest=digest,
                                   changes=changes, image_digest=image_digest,
                                   size_bytes=layer.size_bytes))
        layers.append(layer)
        parent_key = key

    return BuildResult(name=script.name, layers=layers,
                       image_digest=tree_digest(fs), fs=fs)
