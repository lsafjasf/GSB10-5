"""构建脚本解析器。

支持的脚本格式（纯文本，# 开头为注释）::

    FROM ubuntu:22.04
    RUN apt-get update && apt-get install -y curl
    COPY src/app.py /app/app.py
    ENV APP_HOME=/app
    WORKDIR /app
    LABEL version=1
    RUN mkdir -p data
    ...
    --- CONTEXT ---
    {"files": [{"path": "src/app.py", "content": "print(1)\n",
                "mode": 420, "mtime": 1700000000, "uid": 1000, "gid": 1000}]}

"--- CONTEXT ---" 之后为构建上下文清单（JSON）。mode 为十进制整数。
content 按 UTF-8 解释；也支持 content_b64（标准 base64）提供二进制内容。
"""

from __future__ import annotations

import base64
import json
import re
from typing import Iterable, List, Sequence, Tuple, Union

from .model import BuildScript, FileRecord, INSTRUCTION_OPS, Instruction

CONTEXT_MARKER = "--- CONTEXT ---"
_WS_RE = re.compile(r"\s+")


def canonical_arg(arg: str) -> str:
    """指令参数的规范化：去首尾空白、连续空白压成单个空格。

    与 shell 一致，空白分隔 token，因此压缩空白不改变语义，
    而大小写、字符内容、引号等保持不变。
    """
    return _WS_RE.sub(" ", arg.strip())


def parse_script(text: str, name: str = "inline") -> BuildScript:
    parts = text.split(CONTEXT_MARKER, 1)
    body = parts[0]
    context_raw = parts[1] if len(parts) == 2 else ""

    instructions: List[Instruction] = []
    for line_no, raw in enumerate(body.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head, _, arg = line.partition(" ")
        op = head.upper()
        if op not in INSTRUCTION_OPS:
            raise ValueError(f"{name}:{line_no} 不支持的指令: {head!r}")
        instructions.append(Instruction(op=op, arg=canonical_arg(arg)))

    records: List[FileRecord] = []
    if context_raw.strip():
        data = json.loads(context_raw)
        for item in data.get("files", []):
            if "content_b64" in item:
                content: bytes = base64.b64decode(item["content_b64"])
            else:
                content = item.get("content", "").encode("utf-8")
            kind = item.get("kind", "file")
            records.append(
                FileRecord(
                    path=normalize_ctx_path(item["path"]),
                    kind=kind,
                    content=content,
                    mode=int(item.get("mode", 0o644 if kind == "file" else 0o755)),
                    target=item.get("target", ""),
                    mtime=int(item.get("mtime", 0)),
                    uid=int(item.get("uid", 0)),
                    gid=int(item.get("gid", 0)),
                )
            )

    return BuildScript(
        name=name,
        instructions=tuple(instructions),
        context=tuple(sorted(records, key=lambda r: r.path)),
    )


def parse_file(path: str, name: Union[str, None] = None) -> BuildScript:
    with open(path, "r", encoding="utf-8") as fh:
        return parse_script(fh.read(), name=name or path)


def normalize_ctx_path(path: str) -> str:
    """上下文路径规范化：转为以 / 为根、去掉重复斜杠与尾部斜杠。"""
    p = path.replace("\\", "/")
    if not p.startswith("/"):
        p = "/" + p
    p = re.sub(r"/+", "/", p)
    if len(p) > 1:
        p = p.rstrip("/")
    return p
