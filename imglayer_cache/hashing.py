"""规范化哈希：缓存键、指令指纹、节点指纹、层差异摘要、文件系统快照摘要。

所有摘要都是 SHA-256，统一使用长度前缀编码（Canon），避免拼接歧义。
每个域带独立 domain 前缀（node-v1 / snapshot-v1 / op-*-v1 / key-*-v1 /
diff-v1），不同用途的摘要永远不会意外相等。

关键取舍（与需求逐条对应）：
- 节点指纹只包含 kind / mode / target / content，**不包含** mtime、uid、gid；
- 指令指纹包含“父层链”（见 layer_key），因此某层变化会让其后所有层键变化；
- COPY 指纹包含源文件的节点指纹，内容或权限变化必然导致键变化（不会误复用）；
- RUN 指纹包含命令原文、当前 WORKDIR 与排序后的 ENV，保证环境语义参与计算。
"""
from __future__ import annotations

import hashlib

from .model import FileRecord


class Canon:
    """长度前缀的规范化摘要构建器。"""

    def __init__(self, domain: str):
        self.h = hashlib.sha256()
        self.h.update(b"imglayer-cache-v1\n")
        self.bytes(domain.encode("utf-8"))

    def bytes(self, value: bytes) -> "Canon":
        self.h.update(len(value).to_bytes(8, "big"))
        self.h.update(value)
        return self

    def text(self, value: str) -> "Canon":
        return self.bytes(value.encode("utf-8"))

    def integer(self, value: int) -> "Canon":
        self.bytes(int(value).to_bytes(8, "big", signed=True))
        return self

    def digest_value(self, hex_or_bytes: str | bytes) -> "Canon":
        self.bytes(bytes.fromhex(hex_or_bytes) if isinstance(hex_or_bytes, str) else hex_or_bytes)
        return self

    def hexdigest(self) -> str:
        return self.h.hexdigest()


def node_digest(record: FileRecord) -> str:
    """单个文件系统节点的内容指纹（时间戳/所有者不参与）。"""
    canon = Canon("node-v1")
    canon.text(record.kind)
    canon.integer(record.mode & 0o7777)
    if record.kind == "symlink":
        canon.bytes(record.target or b"")
    else:
        canon.bytes(record.content)
    return canon.hexdigest()


def snapshot_digest(files: dict[str, FileRecord]) -> str:
    """整个文件系统视图的摘要：路径排序后逐节点取指纹。

    用于断言“复用层叠加后的最终视图 == 重新构建的最终视图”。
    """
    canon = Canon("snapshot-v1")
    paths = sorted(files)
    canon.integer(len(paths))
    for path in paths:
        canon.text(path)
        canon.digest_value(node_digest(files[path]))
    return canon.hexdigest()


def copy_op_digest(src: str, dst: str, mode: int | None, source: FileRecord) -> str:
    canon = Canon("op-copy-v1")
    canon.text(src)
    canon.text(dst)
    canon.integer(-1 if mode is None else mode)
    canon.digest_value(node_digest(source))
    return canon.hexdigest()


def run_op_digest(command: str, workdir: str, env: dict[str, str]) -> str:
    canon = Canon("op-run-v1")
    canon.text(command.strip())
    canon.text(workdir)
    canon.integer(len(env))
    for key in sorted(env):
        canon.text(key)
        canon.text(env[key])
    return canon.hexdigest()


def workdir_op_digest(path: str) -> str:
    return Canon("op-workdir-v1").text(path).hexdigest()


def env_op_digest(key: str, value: str) -> str:
    return Canon("op-env-v1").text(key).text(value).hexdigest()


def root_key(base_image: str, rootfs_digest: str) -> str:
    """第 0 层之前的键：基础镜像名 + 基础镜像根文件系统摘要。"""
    return Canon("key-root-v1").text(base_image).digest_value(rootfs_digest).hexdigest()


def layer_key(parent_key: str, op_digest_value: str) -> str:
    """层缓存键 = H(父层缓存键, 本层指令指纹)。

    父层键递归包含更早的全部层，因此任意上游层变化都会改变本层键。
    """
    return Canon("key-layer-v1").digest_value(parent_key).digest_value(op_digest_value).hexdigest()


def diff_digest(changes: dict[str, FileRecord | None]) -> str:
    """层差异（相对父层的增/改/删）摘要；None 表示删除。"""
    canon = Canon("diff-v1")
    paths = sorted(changes)
    canon.integer(len(paths))
    for path in paths:
        canon.text(path)
        record = changes[path]
        if record is None:
            canon.text("deleted")
        else:
            canon.text("present")
            canon.digest_value(node_digest(record))
    return canon.hexdigest()
